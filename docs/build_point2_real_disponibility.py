"""Point 2 paper — Mesure de la disponibilité réelle (avec rejet SO).

Re-run la cascade étendue (N=1,3,5,8,12) avec le mécanisme de rejet
de SO activé. Compare :

  - Disponibilité OF-level (% OFs clôturés) — biaisée vers 100%
  - Disponibilité SO-level (% SOs livrées avant due_date+14j)

Produit :
  - docs/charts/point2_real_disponibility.png
  - docs/cadrage_v4_point2_data.md
"""

from __future__ import annotations

import statistics
from pathlib import Path
from tempfile import TemporaryDirectory

import matplotlib.pyplot as plt
import numpy as np

from pilotage_flux.comparative.kpis import compute_kpis
from pilotage_flux.comparative.resilience import _build_cascade_scenario
from pilotage_flux.comparative.runner import run_doctrine
from pilotage_flux.comparative.scenario import DOCTRINES
from pilotage_flux.data_factory import DEFAULT_SPEC, generate_random_fixtures


HERE = Path(__file__).resolve().parent
CHARTS_DIR = HERE / "charts"
DATA_MD = HERE / "cadrage_v4_point2_data.md"

DOCTRINE_LABELS = {
    "of": "OF", "flux": "FLUX",
    "of_event": "OF+EVENT", "event": "EVENT",
}
COLORS = {
    "of": "#888888", "flux": "#1f77b4",
    "of_event": "#ff7f0e", "event": "#2ca02c",
}

CASCADE_LEVELS = [1, 3, 5, 8, 12]
SEEDS = list(range(900, 915))   # 15 seeds → 5×15×4 = 300 runs
TOLERANCE_LEVELS = [0, 3, 14]   # 3 profils : strict, modéré, tolérant


def run_study() -> dict:
    """results[tolerance][n_bd][doctrine] = {so_level: [...], cost: [...]}."""
    total = (
        len(TOLERANCE_LEVELS) * len(CASCADE_LEVELS)
        * len(SEEDS) * len(DOCTRINES)
    )
    print(f"=== Point 2 — disponibilité réelle (3 tolérances) ===")
    print(f"Total : {total} runs")
    results: dict = {
        tol: {
            n_bd: {d: {"so_level": [], "cost": []} for d in DOCTRINES}
            for n_bd in CASCADE_LEVELS
        }
        for tol in TOLERANCE_LEVELS
    }

    with TemporaryDirectory(prefix="point2_") as tmp:
        work = Path(tmp)
        fix_dir = work / "fix"
        generate_random_fixtures(DEFAULT_SPEC, seed=42, out_dir=fix_dir)

        for tol in TOLERANCE_LEVELS:
            print(f"\n=== Tolérance = {tol} jours ===")
            for n_bd in CASCADE_LEVELS:
                print(f"→ Cascade N={n_bd}")
                for seed in SEEDS:
                    scen = _build_cascade_scenario(seed, n_bd, fix_dir)
                    for d in DOCTRINES:
                        db_path = work / f"p2_t{tol}_{n_bd}_{seed}_{d}.db"
                        result = run_doctrine(
                            scen, d, db_path, fixtures_dir=fix_dir,
                            evaluate_rejections=True,
                            late_threshold_days=tol,
                        )
                        kpi = compute_kpis(scen, result)
                        results[tol][n_bd][d]["so_level"].append(
                            kpi.disponibility_so_level
                        )
                        results[tol][n_bd][d]["cost"].append(
                            kpi.total_cost_eur
                        )

    return results


def chart_disponibility(results: dict) -> None:
    """3 panneaux : disponibilité SO-level pour 3 tolérances."""
    fig, axes = plt.subplots(1, 3, figsize=(17, 5.5))
    fig.suptitle(
        "Point 2 — Disponibilité SO-level sous 3 profils de tolérance "
        "(strict / modéré / tolérant)",
        fontsize=13, fontweight="bold",
    )

    tolerance_labels = {
        0: "Strict (livraison ontime)",
        3: "Modéré (+3 j tolérés)",
        14: "Tolérant (+14 j tolérés)",
    }

    ns = sorted(next(iter(results.values())).keys())
    for ax, tol in zip(axes, sorted(results.keys())):
        for d in DOCTRINES:
            so_means = [
                statistics.mean(results[tol][n][d]["so_level"]) * 100
                for n in ns
            ]
            ax.plot(ns, so_means, marker="s", linewidth=2,
                    color=COLORS[d], label=DOCTRINE_LABELS[d])
        ax.set_xlabel("Pannes simultanées (jour 3)")
        ax.set_ylabel("% SOs livrées dans tolérance")
        ax.set_title(f"Tolérance = {tol} jours\n{tolerance_labels[tol]}",
                     fontsize=11)
        ax.set_ylim(0, 105)
        ax.legend(loc="lower left", fontsize=9)
        ax.grid(alpha=0.3)
        ax.set_xticks(ns)

    plt.tight_layout()
    out = CHARTS_DIR / "point2_real_disponibility.png"
    fig.savefig(out, dpi=130, bbox_inches="tight")
    plt.close(fig)
    print(f"✓ {out}")


def write_data(results: dict) -> None:
    lines = ["# Point 2 — Disponibilité SO-level (3 tolérances)", ""]
    for tol in sorted(results.keys()):
        lines.append(f"## Tolérance = {tol} jours")
        lines.append("")
        lines.append(
            "| N pannes | OF | FLUX | OF+EVENT | EVENT |"
        )
        lines.append("|---|---|---|---|---|")
        ns = sorted(results[tol].keys())
        for n in ns:
            row = [str(n)]
            for d in DOCTRINES:
                so_l = statistics.mean(
                    results[tol][n][d]["so_level"]
                ) * 100
                row.append(f"{so_l:.1f} %")
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")
    DATA_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓ {DATA_MD}")


def main() -> None:
    results = run_study()
    chart_disponibility(results)
    write_data(results)
    print("\nPoint 2 — disponibilité réelle terminée.")


if __name__ == "__main__":
    main()
