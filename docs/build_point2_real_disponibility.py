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


def run_study() -> dict[int, dict[str, dict[str, list[float]]]]:
    """results[n_bd][doctrine] = {of_level: [], so_level: []}."""
    print(f"=== Point 2 — disponibilité réelle ===")
    print(f"Total : {len(CASCADE_LEVELS) * len(SEEDS) * len(DOCTRINES)} runs")
    results = {n_bd: {d: {"of_level": [], "so_level": [], "cost": []}
                       for d in DOCTRINES}
                for n_bd in CASCADE_LEVELS}

    with TemporaryDirectory(prefix="point2_") as tmp:
        work = Path(tmp)
        fix_dir = work / "fix"
        generate_random_fixtures(DEFAULT_SPEC, seed=42, out_dir=fix_dir)

        for n_bd in CASCADE_LEVELS:
            print(f"\n→ Cascade N={n_bd} pannes")
            for seed in SEEDS:
                scen = _build_cascade_scenario(seed, n_bd, fix_dir)
                for d in DOCTRINES:
                    db_path = work / f"p2_{n_bd}_{seed}_{d}.db"
                    result = run_doctrine(
                        scen, d, db_path, fixtures_dir=fix_dir,
                        evaluate_rejections=True,
                    )
                    kpi = compute_kpis(scen, result)
                    of_level = (
                        kpi.of_closed / kpi.of_total
                        if kpi.of_total > 0 else 1.0
                    )
                    results[n_bd][d]["of_level"].append(of_level)
                    results[n_bd][d]["so_level"].append(
                        kpi.disponibility_so_level
                    )
                    results[n_bd][d]["cost"].append(kpi.total_cost_eur)

    return results


def chart_disponibility(results: dict) -> None:
    """2 panneaux : disponibilité OF-level vs SO-level."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle(
        "Point 2 — Disponibilité OF-level vs SO-level (biais du simulateur révélé)",
        fontsize=13, fontweight="bold",
    )

    ns = sorted(results.keys())
    for d in DOCTRINES:
        of_means = [
            statistics.mean(results[n][d]["of_level"]) * 100 for n in ns
        ]
        so_means = [
            statistics.mean(results[n][d]["so_level"]) * 100 for n in ns
        ]
        ax1.plot(ns, of_means, marker="o", linewidth=2,
                  color=COLORS[d], label=DOCTRINE_LABELS[d])
        ax2.plot(ns, so_means, marker="s", linewidth=2,
                  color=COLORS[d], label=DOCTRINE_LABELS[d])

    for ax, title, ylabel in [
        (ax1, "Disponibilité OF-level (existant — biaisée)",
         "% OFs clôturés / OFs créés"),
        (ax2, "Disponibilité SO-level (Point 2 — réelle)",
         "% SOs livrées dans due_date + 14j / SOs total"),
    ]:
        ax.set_xlabel("Nombre de pannes simultanées au jour 3")
        ax.set_ylabel(ylabel)
        ax.set_title(title, fontsize=11)
        ax.set_ylim(0, 105)
        ax.legend(loc="lower left")
        ax.grid(alpha=0.3)
        ax.set_xticks(ns)

    plt.tight_layout()
    out = CHARTS_DIR / "point2_real_disponibility.png"
    fig.savefig(out, dpi=130, bbox_inches="tight")
    plt.close(fig)
    print(f"✓ {out}")


def write_data(results: dict) -> None:
    lines = ["# Point 2 — Données brutes disponibilité OF-level vs SO-level", ""]
    lines.append(
        "| N pannes | Doctrine | Dispo OF-level | Dispo SO-level | "
        "Différentiel | Coût moyen |"
    )
    lines.append("|---|---|---|---|---|---|")
    ns = sorted(results.keys())
    for n in ns:
        for d in DOCTRINES:
            of_l = statistics.mean(results[n][d]["of_level"]) * 100
            so_l = statistics.mean(results[n][d]["so_level"]) * 100
            cost = statistics.mean(results[n][d]["cost"])
            diff = of_l - so_l
            row = (
                f"| {n} | {DOCTRINE_LABELS[d]} | "
                f"{of_l:.1f} % | **{so_l:.1f} %** | "
                f"{diff:+.1f} pp | {cost:,.0f} €".replace(",", " ") + " |"
            )
            lines.append(row)
        lines.append("|---|---|---|---|---|---|")
    DATA_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓ {DATA_MD}")


def main() -> None:
    results = run_study()
    chart_disponibility(results)
    write_data(results)
    print("\nPoint 2 — disponibilité réelle terminée.")


if __name__ == "__main__":
    main()
