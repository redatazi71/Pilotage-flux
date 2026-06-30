"""V12.1 — Forecasters time series classiques (ARIMA, Holt-Winters).

Implémentations à partir de statsmodels :

  - ArimaForecaster                : ARIMA(p, d, q) avec auto-ordres
  - ExponentialSmoothingForecaster : Holt-Winters additif

Les deux exposent l'API `fit(series).predict(n_steps)` cohérente
avec les linear.py forecasters.
"""

from __future__ import annotations

import warnings

import numpy as np

from pilotage_flux.cybernetic.forecasting.forecast_result import (
    ForecastResult,
)


class ArimaForecaster:
    """ARIMA(p, d, q) — ordres par défaut (1, 1, 1) adaptés à des
    séries de demande quotidienne avec tendance.
    """

    METHOD_NAME = "arima"

    def __init__(self, order: tuple[int, int, int] = (1, 1, 1)) -> None:
        self._order = order
        self._fitted_model = None
        self._n_train = 0

    def fit(self, series: list[float]) -> "ArimaForecaster":
        from statsmodels.tsa.arima.model import ARIMA
        if len(series) < max(self._order) + 5:
            raise ValueError(
                f"Série trop courte pour ARIMA{self._order} : "
                f"besoin de >= {max(self._order) + 5} pts"
            )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self._fitted_model = ARIMA(
                np.array(series, dtype=float), order=self._order,
            ).fit()
        self._n_train = len(series)
        return self

    def predict(self, n_steps: int) -> ForecastResult:
        if self._fitted_model is None:
            raise RuntimeError("fit() doit être appelé avant predict()")
        if n_steps <= 0:
            raise ValueError("n_steps doit être > 0")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            forecast = self._fitted_model.get_forecast(steps=n_steps)
            mean = forecast.predicted_mean
            ci = forecast.conf_int(alpha=0.05)
        return ForecastResult(
            values=np.asarray(mean, dtype=float).tolist(),
            lower_ci=np.asarray(ci[:, 0], dtype=float).tolist(),
            upper_ci=np.asarray(ci[:, 1], dtype=float).tolist(),
            method_name=f"{self.METHOD_NAME}{self._order}",
            metadata={
                "order": self._order,
                "aic": float(self._fitted_model.aic),
                "bic": float(self._fitted_model.bic),
                "n_train": self._n_train,
            },
        )


class ExponentialSmoothingForecaster:
    """Holt-Winters additif — capture trend + saisonnalité optionnelle.

    Parameters
    ----------
    seasonal_periods : int | None
        Période de saisonnalité (typiquement 7 pour journalier, 12 pour
        mensuel). None → lissage simple sans saisonnalité.
    trend : bool
        Si True, ajoute la composante trend (Holt). Si False, lissage
        exponentiel simple.
    """

    METHOD_NAME = "exp_smoothing"

    def __init__(
        self,
        seasonal_periods: int | None = 7,
        trend: bool = True,
    ) -> None:
        self._seasonal_periods = seasonal_periods
        self._trend = trend
        self._fitted_model = None
        self._n_train = 0
        self._residual_std: float = 0.0

    def fit(self, series: list[float]) -> "ExponentialSmoothingForecaster":
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
        # Holt-Winters demande 2 cycles complets pour la saisonnalité
        min_required = (
            self._seasonal_periods * 2
            if self._seasonal_periods else 5
        )
        if len(series) < min_required:
            raise ValueError(
                f"Série trop courte pour ETS : besoin de >= {min_required} pts"
            )
        kwargs: dict = {}
        if self._trend:
            kwargs["trend"] = "add"
        if self._seasonal_periods:
            kwargs["seasonal"] = "add"
            kwargs["seasonal_periods"] = self._seasonal_periods
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self._fitted_model = ExponentialSmoothing(
                np.array(series, dtype=float), **kwargs,
            ).fit(optimized=True)
        residuals = self._fitted_model.resid
        self._residual_std = (
            float(np.std(residuals, ddof=1)) if len(residuals) > 1 else 0.0
        )
        self._n_train = len(series)
        return self

    def predict(self, n_steps: int) -> ForecastResult:
        if self._fitted_model is None:
            raise RuntimeError("fit() doit être appelé avant predict()")
        if n_steps <= 0:
            raise ValueError("n_steps doit être > 0")
        forecast = self._fitted_model.forecast(steps=n_steps)
        forecast_arr = np.asarray(forecast, dtype=float)
        # ETS de statsmodels ne fournit pas directement d'IC ;
        # on approxime par ±1.96 σ_résidus
        margin = 1.96 * self._residual_std
        return ForecastResult(
            values=forecast_arr.tolist(),
            lower_ci=(forecast_arr - margin).tolist(),
            upper_ci=(forecast_arr + margin).tolist(),
            method_name=(
                f"{self.METHOD_NAME}"
                f"(trend={self._trend},"
                f"seasonal={self._seasonal_periods})"
            ),
            metadata={
                "trend": self._trend,
                "seasonal_periods": self._seasonal_periods,
                "residual_std": self._residual_std,
                "n_train": self._n_train,
                "aic": float(self._fitted_model.aic),
            },
        )
