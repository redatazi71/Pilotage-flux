"""Génère docs/charts/paper_fig4_forecasting_demo.png — démo V12.1.

Affiche les prévisions des 4 forecasters + ensemble sur une série
synthétique trend + saison + bruit, avec intervalles de confiance.
"""

from __future__ import annotations

import math
import random
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from pilotage_flux.cybernetic.forecasting import (
    ArimaForecaster,
    EnsembleForecaster,
    ExponentialSmoothingForecaster,
    LinearTrendForecaster,
    RegressionForecaster,
    rmse,
    split_holdout,
)


HERE = Path(__file__).resolve().parent
CHARTS_DIR = HERE / "charts"


def main() -> None:
    # Série synthétique : 60 jours, trend + saison 7j + bruit
    random.seed(42)
    n = 60
    series = [
        100 + 0.5 * t + 10 * math.sin(2 * math.pi * t / 7)
        + random.gauss(0, 2.5)
        for t in range(n)
    ]
    train, holdout = split_holdout(series, holdout_size=14)

    forecasters_specs = [
        ("Linear trend", LinearTrendForecaster(), "#888888"),
        ("Regression+lags", RegressionForecaster(), "#1f77b4"),
        ("ARIMA(1,1,1)", ArimaForecaster(order=(1, 1, 1)), "#ff7f0e"),
        ("Holt-Winters", ExponentialSmoothingForecaster(seasonal_periods=7),
         "#9467bd"),
    ]
    fitted = []
    for label, f, color in forecasters_specs:
        f.fit(train)
        fitted.append((label, f, color))

    ens = EnsembleForecaster(
        [f for _, f, _ in fitted],
        weighting="inverse_rmse", holdout_size=10,
    ).fit(train)

    n_pred = 14
    fig, ax = plt.subplots(figsize=(14, 6))

    # Train + holdout
    x_train = list(range(len(train)))
    x_holdout = list(range(len(train), n))
    ax.plot(x_train, train, color="black", linewidth=1.5,
            label="Série observée (train)", marker="o", markersize=3)
    ax.plot(x_holdout, holdout, color="black", linewidth=2,
            label="Vérité (holdout)", marker="o", markersize=4,
            linestyle="--")
    ax.axvline(len(train) - 0.5, color="grey", linestyle=":", alpha=0.6)

    # Prédictions par forecaster
    x_pred = list(range(len(train), len(train) + n_pred))
    metrics_rmse = []
    for label, f, color in fitted:
        r = f.predict(n_pred)
        ax.plot(x_pred, r.values, color=color, linewidth=2,
                label=f"{label} (RMSE={rmse(holdout, r.values):.2f})",
                alpha=0.85)
        # IC seulement pour HW (les autres se chevauchent trop)
        if label == "Holt-Winters" and r.lower_ci is not None:
            ax.fill_between(x_pred, r.lower_ci, r.upper_ci,
                            color=color, alpha=0.10)
        metrics_rmse.append((label, rmse(holdout, r.values)))

    # Ensemble
    ens_pred = ens.predict(n_pred)
    ens_rmse = rmse(holdout, ens_pred.values)
    ax.plot(x_pred, ens_pred.values, color="#2ca02c", linewidth=3,
            label=f"Ensemble inv-RMSE (RMSE={ens_rmse:.2f})",
            linestyle="-", marker="s", markersize=5)

    ax.set_xlabel("Jour")
    ax.set_ylabel("Demande (unités)")
    ax.set_title(
        "Figure 4 — V12.1 forecasting zone libre : 4 forecasters + ensemble\n"
        "Série synthétique trend + saisonnalité 7j + bruit gaussien (σ=2.5)",
        fontsize=12, fontweight="bold",
    )
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(alpha=0.3)

    plt.tight_layout()
    out = CHARTS_DIR / "paper_fig4_forecasting_demo.png"
    fig.savefig(out, dpi=130, bbox_inches="tight")
    plt.close(fig)
    print(f"✓ {out}")
    print(f"  Ensemble RMSE = {ens_rmse:.2f}")
    print(f"  Poids ensemble = {[f'{w:.3f}' for w in ens._weights]}")


if __name__ == "__main__":
    main()
