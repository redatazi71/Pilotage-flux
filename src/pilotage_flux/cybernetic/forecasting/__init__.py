"""V12.1 — Forecasting zone libre (horizon long, > 4 semaines).

Cette couche fournit des prévisions statistiques (linéaires et non
linéaires) pour la zone libre du planning, qui sert ensuite d'entrée
à V12.2 (optimisation zone négociable) et à V12.5 (matrice
d'orchestration).

Forecasters fournis :

  - LinearTrendForecaster        : np.polyfit linéaire
  - RegressionForecaster         : sklearn linear regression avec features
  - ArimaForecaster              : statsmodels ARIMA (p,d,q)
  - ExponentialSmoothingForecaster : Holt-Winters
  - EnsembleForecaster           : moyenne (ou pondérée) des précédents

Tous exposent la même API :

  - fit(series: list[float]) -> self
  - predict(n_steps: int) -> ForecastResult
  - score(holdout: list[float]) -> dict[str, float] (MAE, RMSE, MAPE)

ForecastResult contient: values, lower_ci, upper_ci, method_name.
"""

from pilotage_flux.cybernetic.forecasting.dataset import (
    TimeSeriesDataset,
    split_holdout,
)
from pilotage_flux.cybernetic.forecasting.ensemble import EnsembleForecaster
from pilotage_flux.cybernetic.forecasting.feedback import (
    BiasCorrectionWrapper,
    HazardAwareRegressionForecaster,
    HistoricalContext,
)
from pilotage_flux.cybernetic.forecasting.forecast_result import ForecastResult
from pilotage_flux.cybernetic.forecasting.linear import (
    LinearTrendForecaster,
    RegressionForecaster,
)
from pilotage_flux.cybernetic.forecasting.metrics import mae, mape, rmse
from pilotage_flux.cybernetic.forecasting.timeseries import (
    ArimaForecaster,
    ExponentialSmoothingForecaster,
)

__all__ = [
    "ForecastResult",
    "TimeSeriesDataset",
    "split_holdout",
    "LinearTrendForecaster",
    "RegressionForecaster",
    "ArimaForecaster",
    "ExponentialSmoothingForecaster",
    "EnsembleForecaster",
    "HistoricalContext",
    "BiasCorrectionWrapper",
    "HazardAwareRegressionForecaster",
    "mae",
    "rmse",
    "mape",
]
