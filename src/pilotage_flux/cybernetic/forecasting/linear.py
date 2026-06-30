"""V12.1 — Forecasters linéaires (régression, trend).

Deux implémentations :

  - LinearTrendForecaster   : np.polyfit ordre 1 sur l'index temporel
  - RegressionForecaster    : sklearn LinearRegression avec features
                              (trend + lag + jour de la semaine)

Les deux exposent la même API `fit(series).predict(n_steps)`.
"""

from __future__ import annotations

import numpy as np

from pilotage_flux.cybernetic.forecasting.forecast_result import (
    ForecastResult,
)


class LinearTrendForecaster:
    """Régression linéaire sur l'index temporel via numpy.polyfit."""

    METHOD_NAME = "linear_trend"

    def __init__(self) -> None:
        self._slope: float = 0.0
        self._intercept: float = 0.0
        self._residual_std: float = 0.0
        self._n_train: int = 0
        self._fitted: bool = False

    def fit(self, series: list[float]) -> "LinearTrendForecaster":
        if len(series) < 2:
            raise ValueError("Série trop courte : N >= 2 requis")
        x = np.arange(len(series), dtype=float)
        y = np.array(series, dtype=float)
        slope, intercept = np.polyfit(x, y, deg=1)
        self._slope = float(slope)
        self._intercept = float(intercept)
        residuals = y - (slope * x + intercept)
        self._residual_std = (
            float(np.std(residuals, ddof=1)) if len(residuals) > 1 else 0.0
        )
        self._n_train = len(series)
        self._fitted = True
        return self

    def predict(self, n_steps: int) -> ForecastResult:
        if not self._fitted:
            raise RuntimeError("fit() doit être appelé avant predict()")
        if n_steps <= 0:
            raise ValueError("n_steps doit être > 0")
        future_idx = np.arange(
            self._n_train, self._n_train + n_steps, dtype=float,
        )
        values = self._slope * future_idx + self._intercept
        # IC à 95% ≈ ±1.96 σ
        margin = 1.96 * self._residual_std
        return ForecastResult(
            values=values.tolist(),
            lower_ci=(values - margin).tolist(),
            upper_ci=(values + margin).tolist(),
            method_name=self.METHOD_NAME,
            metadata={
                "slope": self._slope,
                "intercept": self._intercept,
                "residual_std": self._residual_std,
                "n_train": self._n_train,
            },
        )


class RegressionForecaster:
    """sklearn LinearRegression avec features :
       - index temporel (trend)
       - 3 lags récents (auto-régression naïve)
       - sin/cos d'une période hebdomadaire (saisonnalité 7j)
    """

    METHOD_NAME = "regression_with_lags_seasonal"
    _LAGS = (1, 2, 3)
    _SEASONALITY = 7.0  # période hebdo

    def __init__(self) -> None:
        from sklearn.linear_model import LinearRegression
        self._model = LinearRegression()
        self._series: list[float] = []
        self._fitted = False
        self._residual_std: float = 0.0

    def _make_features(self, series: list[float]) -> np.ndarray:
        """Construit les features pour les indices où tous les lags
        sont disponibles."""
        max_lag = max(self._LAGS)
        n = len(series)
        rows = []
        for t in range(max_lag, n):
            feats = [float(t)]
            for lag in self._LAGS:
                feats.append(float(series[t - lag]))
            feats.append(np.sin(2 * np.pi * t / self._SEASONALITY))
            feats.append(np.cos(2 * np.pi * t / self._SEASONALITY))
            rows.append(feats)
        return np.array(rows)

    def fit(self, series: list[float]) -> "RegressionForecaster":
        if len(series) < max(self._LAGS) + 5:
            raise ValueError(
                f"Série trop courte : besoin de >= {max(self._LAGS) + 5} pts"
            )
        max_lag = max(self._LAGS)
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
        if n_steps <= 0:
            raise ValueError("n_steps doit être > 0")
        extended = list(self._series)
        future = []
        for _ in range(n_steps):
            t = len(extended)
            feats = [float(t)]
            for lag in self._LAGS:
                feats.append(float(extended[t - lag]))
            feats.append(np.sin(2 * np.pi * t / self._SEASONALITY))
            feats.append(np.cos(2 * np.pi * t / self._SEASONALITY))
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
                "coefficients": self._model.coef_.tolist(),
                "intercept": float(self._model.intercept_),
                "residual_std": self._residual_std,
                "lags": list(self._LAGS),
                "seasonality": self._SEASONALITY,
            },
        )
