"""V12.1 — Ensemble de forecasters.

Combine plusieurs forecasters individuels par moyenne arithmétique
ou pondérée par leur performance sur un holdout.

Pourquoi ensemble : les méthodes statistiques (régression, ARIMA, ETS)
ont des biais différents — combiner leurs prévisions réduit
l'erreur sur des séries où une seule famille de modèle est mal
adaptée. C'est une version simple de **stacking**.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np

from pilotage_flux.cybernetic.forecasting.forecast_result import (
    ForecastResult,
)
from pilotage_flux.cybernetic.forecasting.metrics import rmse


class _Forecaster(Protocol):
    def fit(self, series: list[float]) -> "_Forecaster": ...
    def predict(self, n_steps: int) -> ForecastResult: ...


class EnsembleForecaster:
    """Agrège plusieurs forecasters et combine leurs prédictions.

    Parameters
    ----------
    forecasters : list[_Forecaster]
        Liste de forecasters individuels (déjà construits).
    weighting : str
        "equal" (moyenne simple) ou "inverse_rmse" (pondéré par 1/RMSE
        mesuré sur holdout — exige une série assez longue pour split).
    holdout_size : int
        Taille du holdout utilisé pour calibrer les poids quand
        `weighting='inverse_rmse'`. Ignoré sinon.
    """

    METHOD_NAME = "ensemble"

    def __init__(
        self,
        forecasters: list[_Forecaster],
        *,
        weighting: str = "equal",
        holdout_size: int = 7,
    ) -> None:
        if not forecasters:
            raise ValueError("Au moins 1 forecaster requis")
        if weighting not in {"equal", "inverse_rmse"}:
            raise ValueError(
                f"weighting inconnu : {weighting!r}"
            )
        self._forecasters = forecasters
        self._weighting = weighting
        self._holdout_size = holdout_size
        self._weights: list[float] = []
        self._fitted = False
        self._series: list[float] = []

    def fit(self, series: list[float]) -> "EnsembleForecaster":
        if self._weighting == "equal":
            for f in self._forecasters:
                f.fit(series)
            self._weights = [1.0 / len(self._forecasters)] * len(self._forecasters)
        else:  # inverse_rmse
            if len(series) <= self._holdout_size + 5:
                # Série trop courte pour calibrer → fallback equal
                for f in self._forecasters:
                    f.fit(series)
                self._weights = [1.0 / len(self._forecasters)] * len(self._forecasters)
            else:
                train = series[: -self._holdout_size]
                holdout = series[-self._holdout_size :]
                inv_rmses: list[float] = []
                for f in self._forecasters:
                    try:
                        f.fit(train)
                        pred = f.predict(self._holdout_size).values
                        err = rmse(holdout, pred)
                        inv_rmses.append(1.0 / (err + 1e-9))
                    except Exception:
                        inv_rmses.append(0.0)
                total = sum(inv_rmses) or 1.0
                self._weights = [w / total for w in inv_rmses]
                # Re-fit chaque forecaster sur la série complète
                for f in self._forecasters:
                    f.fit(series)
        self._series = list(series)
        self._fitted = True
        return self

    def predict(self, n_steps: int) -> ForecastResult:
        if not self._fitted:
            raise RuntimeError("fit() doit être appelé avant predict()")
        if n_steps <= 0:
            raise ValueError("n_steps doit être > 0")
        all_values: list[np.ndarray] = []
        all_lower: list[np.ndarray] = []
        all_upper: list[np.ndarray] = []
        method_names: list[str] = []
        for f in self._forecasters:
            r = f.predict(n_steps)
            all_values.append(np.array(r.values))
            if r.lower_ci is not None:
                all_lower.append(np.array(r.lower_ci))
            if r.upper_ci is not None:
                all_upper.append(np.array(r.upper_ci))
            method_names.append(r.method_name)

        w = np.array(self._weights).reshape(-1, 1)
        stacked = np.stack(all_values, axis=0)
        combined = (stacked * w).sum(axis=0)
        # IC combinés = moyenne pondérée des IC individuels (approximation)
        if all_lower:
            lower = (np.stack(all_lower, axis=0) * w).sum(axis=0).tolist()
        else:
            lower = None
        if all_upper:
            upper = (np.stack(all_upper, axis=0) * w).sum(axis=0).tolist()
        else:
            upper = None
        return ForecastResult(
            values=combined.tolist(),
            lower_ci=lower,
            upper_ci=upper,
            method_name=f"{self.METHOD_NAME}({'+'.join(method_names)})",
            metadata={
                "weighting": self._weighting,
                "weights": self._weights,
                "components": method_names,
            },
        )
