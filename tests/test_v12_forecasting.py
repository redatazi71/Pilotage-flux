"""Tests V12.1 — Forecasting zone libre (5 forecasters + ensemble)."""

from __future__ import annotations

import math
import random

import pytest

from pilotage_flux.cybernetic.forecasting import (
    ArimaForecaster,
    EnsembleForecaster,
    ExponentialSmoothingForecaster,
    ForecastResult,
    LinearTrendForecaster,
    RegressionForecaster,
    TimeSeriesDataset,
    mae,
    mape,
    rmse,
    split_holdout,
)


# ---------------------------------------------------------------------
# Données synthétiques
# ---------------------------------------------------------------------


def _make_series(n: int = 60, trend: float = 0.5,
                  amplitude: float = 8.0, noise: float = 3.0,
                  seed: int = 42) -> list[float]:
    """Série trend + saisonnalité 7j + bruit gaussien."""
    rng = random.Random(seed)
    return [
        100 + trend * t + amplitude * math.sin(2 * math.pi * t / 7)
        + rng.gauss(0, noise)
        for t in range(n)
    ]


# ---------------------------------------------------------------------
# Métriques
# ---------------------------------------------------------------------


def test_mae_basic() -> None:
    assert mae([1, 2, 3], [1, 2, 3]) == 0.0
    assert mae([1, 2, 3], [2, 3, 4]) == 1.0


def test_rmse_basic() -> None:
    assert rmse([1, 2, 3], [1, 2, 3]) == 0.0
    assert pytest.approx(rmse([0, 0], [1, 1]), 0.001) == 1.0


def test_mape_skips_zeros() -> None:
    assert mape([0, 100], [10, 110]) == 10.0
    assert mape([0, 0, 0], [1, 1, 1]) == 0.0


def test_metrics_validate_lengths() -> None:
    with pytest.raises(ValueError):
        mae([1, 2], [1])
    with pytest.raises(ValueError):
        rmse([], [])


# ---------------------------------------------------------------------
# split_holdout
# ---------------------------------------------------------------------


def test_split_holdout_separates_correctly() -> None:
    train, hold = split_holdout([1, 2, 3, 4, 5], holdout_size=2)
    assert train == [1, 2, 3]
    assert hold == [4, 5]


def test_split_holdout_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        split_holdout([1, 2, 3], holdout_size=0)
    with pytest.raises(ValueError):
        split_holdout([1, 2, 3], holdout_size=3)


def test_dataset_dataclass() -> None:
    ds = TimeSeriesDataset(values=[1.0, 2.0, 3.0], name="test")
    assert len(ds) == 3
    assert ds.head(2) == [1.0, 2.0]
    assert ds.tail(2) == [2.0, 3.0]


# ---------------------------------------------------------------------
# LinearTrendForecaster
# ---------------------------------------------------------------------


def test_linear_trend_captures_slope() -> None:
    f = LinearTrendForecaster().fit([10.0, 20.0, 30.0, 40.0, 50.0])
    r = f.predict(2)
    assert r.method_name == "linear_trend"
    assert pytest.approx(r.values[0], abs=0.1) == 60.0
    assert pytest.approx(r.values[1], abs=0.1) == 70.0
    assert r.metadata["slope"] == pytest.approx(10.0, abs=0.1)


def test_linear_trend_provides_ci() -> None:
    f = LinearTrendForecaster().fit(_make_series(n=30, noise=5.0))
    r = f.predict(5)
    assert len(r.lower_ci) == 5
    assert len(r.upper_ci) == 5
    for lo, val, hi in zip(r.lower_ci, r.values, r.upper_ci):
        assert lo <= val <= hi


def test_linear_trend_rejects_short() -> None:
    with pytest.raises(ValueError):
        LinearTrendForecaster().fit([1.0])


# ---------------------------------------------------------------------
# RegressionForecaster
# ---------------------------------------------------------------------


def test_regression_beats_random_baseline() -> None:
    series = _make_series(n=60, noise=2.0)
    train, holdout = split_holdout(series, holdout_size=10)
    f = RegressionForecaster().fit(train)
    pred = f.predict(10).values
    # Régression avec features doit faire mieux que la naive avg
    naive_mean = [sum(train) / len(train)] * 10
    assert rmse(holdout, pred) < rmse(holdout, naive_mean)


def test_regression_rejects_short() -> None:
    with pytest.raises(ValueError):
        RegressionForecaster().fit([1.0, 2.0, 3.0])


# ---------------------------------------------------------------------
# ArimaForecaster
# ---------------------------------------------------------------------


def test_arima_fits_and_predicts() -> None:
    series = _make_series(n=40, noise=2.0)
    f = ArimaForecaster(order=(1, 1, 1)).fit(series)
    r = f.predict(5)
    assert r.method_name == "arima(1, 1, 1)"
    assert len(r.values) == 5
    assert r.metadata["aic"] is not None


def test_arima_custom_order() -> None:
    series = _make_series(n=50, noise=1.5)
    f = ArimaForecaster(order=(2, 1, 0)).fit(series)
    r = f.predict(3)
    assert r.metadata["order"] == (2, 1, 0)
    assert len(r.values) == 3


# ---------------------------------------------------------------------
# ExponentialSmoothingForecaster
# ---------------------------------------------------------------------


def test_ets_captures_seasonality() -> None:
    series = _make_series(n=60, amplitude=20.0, noise=1.0)
    f = ExponentialSmoothingForecaster(seasonal_periods=7).fit(series)
    r = f.predict(7)
    assert len(r.values) == 7
    # Sur série très saisonnière + faible bruit, Holt-Winters devrait
    # capturer correctement → IC raisonnable
    for v in r.values:
        assert 80 < v < 250


def test_ets_no_trend_no_seasonal() -> None:
    series = [100.0 + random.Random(7).gauss(0, 1) for _ in range(30)]
    f = ExponentialSmoothingForecaster(
        seasonal_periods=None, trend=False,
    ).fit(series)
    r = f.predict(5)
    assert len(r.values) == 5


# ---------------------------------------------------------------------
# EnsembleForecaster
# ---------------------------------------------------------------------


def test_ensemble_equal_weight() -> None:
    series = _make_series(n=50, noise=2.0)
    ens = EnsembleForecaster([
        LinearTrendForecaster(),
        ExponentialSmoothingForecaster(seasonal_periods=7),
    ], weighting="equal").fit(series)
    r = ens.predict(5)
    assert r.method_name.startswith("ensemble(")
    assert len(r.values) == 5
    # equal → poids 0.5 chacun
    assert ens._weights == [0.5, 0.5]


def test_ensemble_inverse_rmse_weights_better_models_higher() -> None:
    series = _make_series(n=60, amplitude=15.0, noise=1.5)
    # Linear trend ne capture pas la saison → mauvais RMSE
    # Holt-Winters capture la saison → meilleur RMSE
    ens = EnsembleForecaster([
        LinearTrendForecaster(),
        ExponentialSmoothingForecaster(seasonal_periods=7),
    ], weighting="inverse_rmse", holdout_size=10).fit(series)
    # Holt-Winters doit avoir un poids strictement supérieur
    assert ens._weights[1] > ens._weights[0]


def test_ensemble_requires_at_least_one() -> None:
    with pytest.raises(ValueError):
        EnsembleForecaster([])


def test_ensemble_invalid_weighting() -> None:
    with pytest.raises(ValueError):
        EnsembleForecaster([LinearTrendForecaster()], weighting="bogus")
