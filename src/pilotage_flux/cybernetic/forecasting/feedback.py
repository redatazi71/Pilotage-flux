"""V12.1.1 — Forecasting feedback-aware (boucle d'apprentissage).

Cette extension ajoute à V12.1 forecasting :

  1. **HistoricalContext** : charge les statistiques d'écarts depuis
     la DB (event_deviations + tolerance_filter_decisions) — base
     commune des composants feedback.

  2. **BiasCorrectionWrapper** : wrapper qui ajuste les prédictions
     d'un base forecaster en soustrayant le biais historique
     observé sur une fenêtre glissante. Si le forecast sur-prédit
     systématiquement, le wrapper corrige automatiquement.

  3. **HazardAwareRegressionForecaster** : régression linéaire
     enrichie de features de comptage d'aléas par type sur les
     N jours précédents. Permet au forecast d'anticiper les jours
     historiquement « à risque ».

Le but est de **fermer la boucle cybernétique** entre les couches
V12.1 (zone libre) et la doctrine V0-V11 (event_deviations).
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from pilotage_flux.cybernetic.forecasting.forecast_result import (
    ForecastResult,
)


# ---------------------------------------------------------------------
# 1. HistoricalContext — interface commune avec la DB
# ---------------------------------------------------------------------

@dataclass
class HistoricalContext:
    """Snapshot lazy-loadé des statistiques d'écarts pour le feedback."""

    conn: sqlite3.Connection
    _hazard_counts_by_kind: dict[str, int] = field(default_factory=dict)
    _bias_cache: dict[str, float] = field(default_factory=dict)
    _loaded: bool = False

    def _load(self) -> None:
        if self._loaded:
            return
        # Compte les déviations par kind
        rows = self.conn.execute(
            """
            SELECT deviation_kind, COUNT(*) AS n
            FROM event_deviations
            GROUP BY deviation_kind
            """
        ).fetchall()
        for r in rows:
            self._hazard_counts_by_kind[r["deviation_kind"]] = int(r["n"])
        self._loaded = True

    def get_hazard_count(self, deviation_kind: str) -> int:
        """Nb total d'écarts d'un kind donné dans l'historique."""
        self._load()
        return self._hazard_counts_by_kind.get(deviation_kind, 0)

    def get_hazard_counts_by_window(
        self,
        deviation_kind: str,
        window_days: int = 7,
        reference_date: datetime | None = None,
    ) -> int:
        """Comptes des déviations sur une fenêtre temporelle glissante."""
        if reference_date is None:
            reference_date = datetime.utcnow()
        cutoff = (reference_date - timedelta(days=window_days)).isoformat()
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS n FROM event_deviations
            WHERE deviation_kind = ? AND detected_at >= ?
            """,
            (deviation_kind, cutoff),
        ).fetchone()
        return int(row["n"]) if row else 0

    def get_recent_deviation_scores(
        self, deviation_kind: str, n: int = 50,
    ) -> list[float]:
        """N scores de magnitude les plus récents pour un kind donné."""
        rows = self.conn.execute(
            """
            SELECT score FROM event_deviations
            WHERE deviation_kind = ? AND score IS NOT NULL
            ORDER BY detected_at DESC LIMIT ?
            """,
            (deviation_kind, n),
        ).fetchall()
        return [float(r["score"]) for r in rows]

    def compute_bias(
        self, observed: list[float], predicted: list[float],
    ) -> float:
        """Calcule le biais moyen = mean(predicted - observed).

        Positif → forecast sur-prédit ; négatif → sous-prédit.
        """
        if not observed or len(observed) != len(predicted):
            return 0.0
        diffs = [p - o for o, p in zip(observed, predicted)]
        return float(np.mean(diffs))

    def get_hazard_density_by_day_of_week(
        self, deviation_kind: str | None = None,
    ) -> dict[int, float]:
        """Densité d'aléas par jour de semaine (0=lundi … 6=dimanche).

        Si `deviation_kind` fourni, filtre. Renvoie un dict
        complet pour les 7 jours (0.0 si absent).
        """
        sql = (
            "SELECT detected_at FROM event_deviations "
            "WHERE detected_at IS NOT NULL"
        )
        params: list = []
        if deviation_kind is not None:
            sql += " AND deviation_kind = ?"
            params.append(deviation_kind)
        rows = self.conn.execute(sql, tuple(params)).fetchall()
        counts: dict[int, int] = defaultdict(int)
        for r in rows:
            try:
                dt = datetime.fromisoformat(r["detected_at"])
            except (ValueError, TypeError):
                continue
            counts[dt.weekday()] += 1
        total = sum(counts.values()) or 1
        return {dow: counts.get(dow, 0) / total for dow in range(7)}


# ---------------------------------------------------------------------
# 2. BiasCorrectionWrapper — ajuste les prédictions d'un base forecaster
# ---------------------------------------------------------------------

class BiasCorrectionWrapper:
    """Wrap un forecaster et soustrait un biais constant à ses prédictions.

    Le biais est typiquement appris en comparant les prédictions
    précédentes du même forecaster aux observations réelles
    (méthode `learn_bias`).
    """

    METHOD_NAME = "bias_corrected"

    def __init__(self, base_forecaster, bias: float = 0.0) -> None:
        self._base = base_forecaster
        self._bias = float(bias)
        self._fitted = False

    @property
    def bias(self) -> float:
        return self._bias

    def learn_bias(
        self, observed: list[float], previous_predictions: list[float],
    ) -> "BiasCorrectionWrapper":
        """Apprend le biais à partir d'une paire (observé, prédit)."""
        if not observed:
            self._bias = 0.0
        else:
            if len(observed) != len(previous_predictions):
                raise ValueError(
                    "observed et previous_predictions de longueurs différentes"
                )
            diffs = [
                p - o for o, p in zip(observed, previous_predictions)
            ]
            self._bias = float(np.mean(diffs))
        return self

    def fit(self, series: list[float]) -> "BiasCorrectionWrapper":
        self._base.fit(series)
        self._fitted = True
        return self

    def predict(self, n_steps: int) -> ForecastResult:
        if not self._fitted:
            raise RuntimeError("fit() doit être appelé avant predict()")
        base_result = self._base.predict(n_steps)
        adjusted = [v - self._bias for v in base_result.values]
        adjusted_lo = (
            [v - self._bias for v in base_result.lower_ci]
            if base_result.lower_ci is not None else None
        )
        adjusted_hi = (
            [v - self._bias for v in base_result.upper_ci]
            if base_result.upper_ci is not None else None
        )
        return ForecastResult(
            values=adjusted,
            lower_ci=adjusted_lo,
            upper_ci=adjusted_hi,
            method_name=f"{self.METHOD_NAME}({base_result.method_name})",
            metadata={
                **base_result.metadata,
                "bias_subtracted": self._bias,
                "base_method": base_result.method_name,
            },
        )


# ---------------------------------------------------------------------
# 3. HazardAwareRegressionForecaster — régression enrichie
# ---------------------------------------------------------------------

class HazardAwareRegressionForecaster:
    """Régression linéaire incluant des features de comptage d'aléas.

    Features par pas temporel `t` :
      - index t (trend)
      - 3 lags sur la série (autorégression)
      - sin/cos d'une période saisonnière (7j par défaut)
      - **NOUVEAU** : densité historique d'aléas pour le `weekday(t)`
      - **NOUVEAU** : densité d'aléas pour les `weekday(t-1)` (rebond)

    Les 2 dernières features sont obtenues via `HistoricalContext`.
    Si le context n'a aucun event_deviation, ces features valent 0
    → l'extrapolation se réduit au baseline RegressionForecaster.
    """

    METHOD_NAME = "hazard_aware_regression"
    _LAGS = (1, 2, 3)
    _SEASONALITY = 7.0

    def __init__(
        self,
        context: HistoricalContext | None = None,
        hazard_kind: str = "time_delta",
        horizon_start: datetime | None = None,
    ) -> None:
        from sklearn.linear_model import LinearRegression
        self._model = LinearRegression()
        self._context = context
        self._hazard_kind = hazard_kind
        self._horizon_start = horizon_start or datetime.utcnow()
        self._series: list[float] = []
        self._density_by_dow: dict[int, float] = {dow: 0.0 for dow in range(7)}
        self._fitted = False
        self._residual_std: float = 0.0

    def _refresh_density(self) -> None:
        if self._context is not None:
            self._density_by_dow = self._context.get_hazard_density_by_day_of_week(
                deviation_kind=self._hazard_kind,
            )

    def _weekday_for_step(self, t: int) -> int:
        return (self._horizon_start + timedelta(days=t)).weekday()

    def _make_features(self, series: list[float]) -> np.ndarray:
        max_lag = max(self._LAGS)
        rows = []
        for t in range(max_lag, len(series)):
            feats = [float(t)]
            for lag in self._LAGS:
                feats.append(float(series[t - lag]))
            feats.append(np.sin(2 * np.pi * t / self._SEASONALITY))
            feats.append(np.cos(2 * np.pi * t / self._SEASONALITY))
            dow_t = self._weekday_for_step(t)
            dow_t_minus_1 = self._weekday_for_step(t - 1)
            feats.append(self._density_by_dow.get(dow_t, 0.0))
            feats.append(self._density_by_dow.get(dow_t_minus_1, 0.0))
            rows.append(feats)
        return np.array(rows)

    def fit(
        self, series: list[float],
    ) -> "HazardAwareRegressionForecaster":
        max_lag = max(self._LAGS)
        if len(series) < max_lag + 5:
            raise ValueError(
                f"Série trop courte : besoin de >= {max_lag + 5} pts"
            )
        self._refresh_density()
        X = self._make_features(series)
        y = np.array(series[max_lag:], dtype=float)
        self._model.fit(X, y)
        residuals = y - self._model.predict(X)
        self._residual_std = (
            float(np.std(residuals, ddof=1)) if len(residuals) > 1 else 0.0
        )
        self._series = list(series)
        self._fitted = True
        return self

    def predict(self, n_steps: int) -> ForecastResult:
        if not self._fitted:
            raise RuntimeError("fit() doit être appelé avant predict()")
        extended = list(self._series)
        future = []
        for _ in range(n_steps):
            t = len(extended)
            feats = [float(t)]
            for lag in self._LAGS:
                feats.append(float(extended[t - lag]))
            feats.append(np.sin(2 * np.pi * t / self._SEASONALITY))
            feats.append(np.cos(2 * np.pi * t / self._SEASONALITY))
            dow_t = self._weekday_for_step(t)
            dow_t_minus_1 = self._weekday_for_step(t - 1)
            feats.append(self._density_by_dow.get(dow_t, 0.0))
            feats.append(self._density_by_dow.get(dow_t_minus_1, 0.0))
            pred = float(self._model.predict([feats])[0])
            future.append(pred)
            extended.append(pred)
        future_arr = np.array(future)
        margin = 1.96 * self._residual_std
        return ForecastResult(
            values=future_arr.tolist(),
            lower_ci=(future_arr - margin).tolist(),
            upper_ci=(future_arr + margin).tolist(),
            method_name=self.METHOD_NAME,
            metadata={
                "lags": list(self._LAGS),
                "seasonality": self._SEASONALITY,
                "hazard_kind": self._hazard_kind,
                "density_by_dow": dict(self._density_by_dow),
                "residual_std": self._residual_std,
            },
        )
