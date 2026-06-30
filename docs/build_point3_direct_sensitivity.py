"""Point 3 paper — Sensibilité directe des paramètres data-driven.

Contrairement à §7.3 qui utilise des proxies (n_hazards comme proxy
d'intensité scrap, horizon_days comme proxy de buffer), ce script
vary directement les paramètres dans la table `parameters` post
seed_defaults.

3 paramètres × 4 niveaux × 4 doctrines × 10 seeds = 480 runs.

Paramètres directs testés :

  1. constraint_buffer_safety_factor (DBR tampon Little)
     - 0.05 (peu protégé), 0.15 (défaut), 0.25 (protégé), 0.35 (extrême)

  2. Seuils Little (warn/block/defer)
     - strict   : (0.60, 0.75, 0.90)
     - défaut   : (0.80, 0.90, 1.10)
     - lâche    : (0.85, 0.95, 1.15)
     - très lâche : (0.95, 0.99, 1.20)

  3. Multiplicateur coût scrap (×0.5, ×1, ×2, ×5)
     - via unit_cost sur articles finis × multiplicateur

Produit :
  - docs/charts/point3_direct_sensitivity.png
  - docs/cadrage_v4_point3_data.md
"""

from __future__ import annotations

import sqlite3
import statistics
from pathlib import Path
from tempfile import TemporaryDirectory

import matplotlib.pyplot as plt

from pilotage_flux.comparative.kpis import compute_kpis
from pilotage_flux.comparative.random_scenario import (
    RandomScenarioSpec,
    generate_random_scenario,
)
from pilotage_flux.comparative.runner import run_doctrine
from pilotage_flux.comparative.scenario import DOCTRINES
from pilotage_flux.data_factory import DEFAULT_SPEC, generate_random_fixtures
from pilotage_flux.db import db_session


HERE = Path(__file__).resolve().parent
CHARTS_DIR = HERE / "charts"
DATA_MD = HERE / "cadrage_v4_point3_data.md"

DOCTRINE_LABELS = {
    "of": "OF", "flux": "FLUX",
    "of_event": "OF+EVENT", "event": "EVENT",
}
COLORS = {
    "of": "#888888", "flux": "#1f77b4",
    "of_event": "#ff7f0e", "event": "#2ca02c",
}

SEEDS = list(range(950, 960))


def override_parameter(
    conn: sqlite3.Connection, scope: str, scope_ref: str | None,
    name: str, value: float,
) -> None:
    """Override un paramètre data-driven via bump de version.

    Ferme la version courante (`valid_to = now()`) et insère une
    nouvelle row avec version incrémentée.
    """
    conn.execute(
        """
        UPDATE parameters SET valid_to = datetime('now')
        WHERE scope = ? AND (scope_ref IS ? OR scope_ref = ?)
          AND name = ? AND valid_to IS NULL
        """,
        (scope, scope_ref, scope_ref, name),
    )
    row = conn.execute(
        """
        SELECT COALESCE(MAX(version), 0) + 1 AS v FROM parameters
        WHERE scope = ? AND (scope_ref IS ? OR scope_ref = ?) AND name = ?
        """,
        (scope, scope_ref, scope_ref, name),
    ).fetchone()
    conn.execute(
        """
        INSERT INTO parameters (scope, scope_ref, name, value_num, version)
        VALUES (?, ?, ?, ?, ?)
        """,
        (scope, scope_ref, name, float(value), int(row["v"])),
    )


def build_buffer_overrides(factor: float) -> dict:
    return {("global", None, "constraint_buffer_safety_factor"): factor}


def build_little_overrides(warn: float, block: float, defer: float) -> dict:
    return {
        ("global", None, "little_threshold_warn"): warn,
        ("global", None, "little_threshold_block"): block,
        ("global", None, "little_threshold_defer"): defer,
    }


def build_scrap_multiplier_overrides(multiplier: float) -> dict:
    """Override le moi_overhead_rate (proxy de coût indirect global,
    fortement corrélé au coût scrap dans le modèle V11).

    Multiplie le taux par `multiplier`. Override appliqué AVANT
    simulation via run_doctrine(param_overrides=...).
    """
    base_rate = 0.20  # défaut V11
    return {("global", None, "moi_overhead_rate"): base_rate * multiplier}


# ---------------------------------------------------------------------
# Étude
# ---------------------------------------------------------------------

LEVELS = ["faible", "moyen", "élevé", "extrême"]

PARAMS_GRID = {
    "Tampon DBR (safety_factor)": {
        "values": [0.05, 0.15, 0.25, 0.35],
        "build": lambda v: build_buffer_overrides(v),
    },
    "Seuils Little (warn/block/defer)": {
        "values": [
            (0.60, 0.75, 0.90),
            (0.80, 0.90, 1.10),
            (0.85, 0.95, 1.15),
            (0.95, 0.99, 1.20),
        ],
        "build": lambda v: build_little_overrides(*v),
    },
    "MOI overhead rate (multiplicateur)": {
        "values": [0.5, 1.0, 2.0, 5.0],
        "build": lambda v: build_scrap_multiplier_overrides(v),
    },
}


def run_study() -> dict:
    """results[param_name][level_idx][doctrine] = [costs]."""
    total = len(PARAMS_GRID) * len(LEVELS) * len(SEEDS) * len(DOCTRINES)
    print(f"=== Point 3 — sensibilité directe : {total} runs ===")
    results = {
        param: {i: {d: [] for d in DOCTRINES} for i in range(len(LEVELS))}
        for param in PARAMS_GRID
    }

    with TemporaryDirectory(prefix="point3_") as tmp:
        work = Path(tmp)
        fix_dir = work / "fix"
        generate_random_fixtures(DEFAULT_SPEC, seed=42, out_dir=fix_dir)

        for param_name, cfg in PARAMS_GRID.items():
            print(f"\n→ {param_name}")
            for level_idx, value in enumerate(cfg["values"]):
                overrides = cfg["build"](value)
                print(f"  {LEVELS[level_idx]} = {value}")
                for seed in SEEDS:
                    scen = generate_random_scenario(
                        RandomScenarioSpec(), seed=seed,
                        fixtures_dir=fix_dir,
                    )
                    for d in DOCTRINES:
                        db_path = (
                            work
                            / f"p3_{level_idx}_{seed}_{d}.db"
                        )
                        result = run_doctrine(
                            scen, d, db_path, fixtures_dir=fix_dir,
                            evaluate_rejections=False,
                            param_overrides=overrides,
                        )
                        kpi = compute_kpis(scen, result)
                        results[param_name][level_idx][d].append(
                            kpi.total_cost_eur
                        )
    return results


def chart_results(results: dict) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(17, 5.5))
    fig.suptitle(
        "Point 3 — Sensibilité directe : 3 paramètres × 4 niveaux "
        "(faible / moyen / élevé / extrême)",
        fontsize=13, fontweight="bold",
    )
    import numpy as np
    x = np.arange(len(LEVELS))
    width = 0.20

    for ax, (param_name, data) in zip(axes, results.items()):
        for j, d in enumerate(DOCTRINES):
            means = [
                statistics.mean(data[i][d]) if data[i][d] else 0
                for i in range(len(LEVELS))
            ]
            ax.bar(x + (j - 1.5) * width, means, width,
                    color=COLORS[d], edgecolor="black",
                    label=DOCTRINE_LABELS[d])
        ax.set_xticks(x)
        ax.set_xticklabels(LEVELS, fontsize=9)
        ax.set_ylabel("Coût moyen (€)")
        ax.set_title(param_name, fontsize=10)
        ax.grid(axis="y", alpha=0.3)
        if axes.tolist().index(ax) == 0:
            ax.legend(loc="upper left", fontsize=8)

    plt.tight_layout()
    out = CHARTS_DIR / "point3_direct_sensitivity.png"
    fig.savefig(out, dpi=130, bbox_inches="tight")
    plt.close(fig)
    print(f"✓ {out}")


def write_data(results: dict) -> None:
    lines = ["# Point 3 — Sensibilité directe (paramètres data-driven)", ""]
    for param_name, data in results.items():
        lines.append(f"## {param_name}")
        lines.append("")
        cfg = PARAMS_GRID[param_name]
        lines.append("| Niveau | Valeur | OF | FLUX | OF+EVENT | EVENT | Δ FLUX/OF |")
        lines.append("|---|---|---|---|---|---|---|")
        for i, level in enumerate(LEVELS):
            val = cfg["values"][i]
            row_vals = []
            for d in DOCTRINES:
                if data[i][d]:
                    row_vals.append(statistics.mean(data[i][d]))
                else:
                    row_vals.append(0)
            of_mean = row_vals[0]
            flux_mean = row_vals[1]
            delta_pct = (flux_mean - of_mean) / of_mean * 100 if of_mean else 0
            row = [
                level, str(val),
                f"{row_vals[0]:,.0f} €".replace(",", " "),
                f"{row_vals[1]:,.0f} €".replace(",", " "),
                f"{row_vals[2]:,.0f} €".replace(",", " "),
                f"{row_vals[3]:,.0f} €".replace(",", " "),
                f"{delta_pct:+.1f} %",
            ]
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")
    DATA_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓ {DATA_MD}")


def main() -> None:
    results = run_study()
    chart_results(results)
    write_data(results)
    print("\nPoint 3 — sensibilité directe terminée.")


if __name__ == "__main__":
    main()
