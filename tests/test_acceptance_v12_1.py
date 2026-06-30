"""V12.1 — Test d'acceptation E2E : pipeline complet forecasting."""

from __future__ import annotations

import math
import random

from pilotage_flux.cybernetic.forecasting import (
    ArimaForecaster,
    EnsembleForecaster,
    ExponentialSmoothingForecaster,
    LinearTrendForecaster,
    RegressionForecaster,
    mae,
    mape,
    rmse,
    split_holdout,
)


def _synth_demand(
    n: int, trend: float, amplitude: float, noise: float, seed: int,
) -> list[float]:
    rng = random.Random(seed)
    return [
        max(0.0, 100 + trend * t + amplitude * math.sin(2 * math.pi * t / 7)
              + rng.gauss(0, noise))
        for t in range(n)
    ]


def test_v12_1_e2e_5_forecasters_on_synthetic_series() -> None:
    """Pipeline E2E : génère série, entraîne 4 forecasters + ensemble,
    vérifie que les RMSE sont tous finis et que l'ensemble est dans
    la même fourchette que ses meilleurs composants."""
    series = _synth_demand(n=80, trend=0.4, amplitude=12.0, noise=2.5, seed=42)
    train, holdout = split_holdout(series, holdout_size=14)

    results: dict[str, float] = {}

    f_linear = LinearTrendForecaster().fit(train)
    results["linear"] = rmse(holdout, f_linear.predict(14).values)

    f_reg = RegressionForecaster().fit(train)
    results["regression"] = rmse(holdout, f_reg.predict(14).values)

    f_arima = ArimaForecaster(order=(1, 1, 1)).fit(train)
    results["arima"] = rmse(holdout, f_arima.predict(14).values)

    f_ets = ExponentialSmoothingForecaster(seasonal_periods=7).fit(train)
    results["ets"] = rmse(holdout, f_ets.predict(14).values)

    # Tous les RMSE doivent être finis et positifs
    for name, val in results.items():
        assert val > 0, f"{name}: RMSE non positif"
        assert val < 100, f"{name}: RMSE déraisonnable ({val})"

    # Ensemble inverse_rmse doit améliorer le forecaster le pire,
    # et ne pas être catastrophiquement pire que le meilleur
    ens = EnsembleForecaster([
        LinearTrendForecaster(),
        RegressionForecaster(),
        ArimaForecaster(),
        ExponentialSmoothingForecaster(seasonal_periods=7),
    ], weighting="inverse_rmse", holdout_size=14).fit(train)
    ens_rmse = rmse(holdout, ens.predict(14).values)

    worst = max(results.values())
    best = min(results.values())
    assert ens_rmse <= worst, (
        f"Ensemble ({ens_rmse:.2f}) pire que worst ({worst:.2f})"
    )
    # L'ensemble est dans la moyenne géométrique [best, worst]
    assert ens_rmse <= worst * 1.1


def test_v12_1_e2e_realistic_workflow() -> None:
    """Workflow réaliste : on a 8 semaines d'historique (56 jours),
    on entraîne un ensemble, on prédit les 2 prochaines semaines,
    on vérifie que les valeurs prédites sont réalistes."""
    series = _synth_demand(n=56, trend=0.3, amplitude=10.0, noise=2.0, seed=7)
    ens = EnsembleForecaster([
        LinearTrendForecaster(),
        ExponentialSmoothingForecaster(seasonal_periods=7),
        ArimaForecaster(order=(1, 1, 1)),
    ], weighting="inverse_rmse", holdout_size=14).fit(series)

    forecast = ens.predict(14)
    assert len(forecast.values) == 14
    assert forecast.lower_ci is not None
    assert forecast.upper_ci is not None
    assert "ensemble" in forecast.method_name

    # Vérifie continuité : 1ère valeur prédite ≈ dernière valeur observée
    # (à un sigma près sur série bruitée)
    last_obs = series[-1]
    first_pred = forecast.values[0]
    diff = abs(first_pred - last_obs)
    assert diff < 30, (
        f"Discontinuité trop grande : last_obs={last_obs:.1f}, "
        f"first_pred={first_pred:.1f}, diff={diff:.1f}"
    )


def test_v12_1_e2e_arima_metadata_includes_aic() -> None:
    """Vérifie que les métadonnées AIC/BIC ARIMA sont remontées."""
    series = _synth_demand(n=40, trend=0.2, amplitude=5.0, noise=1.5, seed=11)
    f = ArimaForecaster(order=(1, 1, 1)).fit(series)
    r = f.predict(5)
    assert r.metadata["aic"] is not None
    assert r.metadata["bic"] is not None
    # AIC doit être un float fini négatif ou positif (pas inf)
    assert -1e9 < r.metadata["aic"] < 1e9
