"""Étude de résilience §24.8 — comble les 5 manques identifiés.

  1. P50/P75/P95/P99 du coût (statistiques d'ordre)
  2. Histogramme de la distribution complète
  3. Gradient performance vs intensité d'aléa
  4. Cascade : N pannes simultanées (1..5)
  5. Time-to-recover après choc (proxy MTTR)

Produit :
  - docs/charts/resilience_distribution.png
  - docs/charts/resilience_gradient.png
  - docs/charts/resilience_cascade.png
  - docs/charts/resilience_mttr.png
  - docs/cadrage_v4_resilience_data.md (table des résultats bruts)

Usage : python docs/build_resilience_analysis.py
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import matplotlib.pyplot as plt
import numpy as np

from pilotage_flux.comparative.resilience import (
    compute_cost_distribution,
    run_cascade_curve,
    run_distribution_grid,
    run_intensity_curve,
)
from pilotage_flux.comparative.scenario import DOCTRINES
from pilotage_flux.data_factory import (
    DEFAULT_SPEC,
    generate_random_fixtures,
)


HERE = Path(__file__).resolve().parent
CHARTS_DIR = HERE / "charts"
CHARTS_DIR.mkdir(parents=True, exist_ok=True)
DATA_MD = HERE / "cadrage_v4_resilience_data.md"

DOCTRINE_LABELS = {
    "of": "OF", "flux": "FLUX",
    "of_event": "OF+EVENT", "event": "EVENT",
}
COLORS = {
    "of": "#888888", "flux": "#1f77b4",
    "of_event": "#ff7f0e", "event": "#2ca02c",
}

# Tailles de grids (réduites pour ~10 min total sur SSD local)
DIST_FIXTURE_SEEDS = list(range(1, 9))     # 8 fixtures
DIST_SCEN_SEEDS = list(range(100, 108))    # 8 scénarios → 8×8×4 = 256 runs
INTENSITY_LEVELS = [0.5, 1.0, 1.5, 2.0, 2.5]
INTENSITY_SEEDS = list(range(200, 215))    # 15 × 5 × 4 = 300 runs
CASCADE_LEVELS = [1, 2, 3, 4, 5]
CASCADE_SEEDS = list(range(300, 315))      # 15 × 5 × 4 = 300 runs


def chart_distribution(distributions: dict) -> None:
    """Box plot + nuage des coûts par doctrine."""
    fig, ax = plt.subplots(figsize=(10, 6))
    doctrines = list(DOCTRINES)
    data = [distributions[d].samples for d in doctrines]
    bp = ax.boxplot(
        data,
        positions=range(len(doctrines)),
        widths=0.55,
        patch_artist=True,
        showfliers=True,
        whis=(5, 95),  # whiskers à P5/P95 — visualisent le tail risk
    )
    for patch, d in zip(bp["boxes"], doctrines):
        patch.set_facecolor(COLORS[d])
        patch.set_alpha(0.6)
    for median in bp["medians"]:
        median.set_color("black")
        median.set_linewidth(1.5)

    # Annoter P95 et P99
    for i, d in enumerate(doctrines):
        dist = distributions[d]
        ax.plot(i, dist.p95, marker="^", color="red", markersize=10, zorder=10)
        ax.plot(i, dist.p99, marker="*", color="darkred", markersize=14, zorder=10)
        ax.text(i + 0.3, dist.p95, f"P95={int(dist.p95):,}".replace(",", " "),
                color="red", fontsize=8, va="center")
        ax.text(i + 0.3, dist.p99, f"P99={int(dist.p99):,}".replace(",", " "),
                color="darkred", fontsize=8, fontweight="bold", va="center")

    ax.set_xticks(range(len(doctrines)))
    ax.set_xticklabels([DOCTRINE_LABELS[d] for d in doctrines])
    ax.set_ylabel("Coût total (€)")
    ax.set_title("§24.8.1 — Distribution des coûts par doctrine\n"
                 "(box: Q1–Q3, whiskers: P5–P95, ▲=P95, ★=P99)",
                 fontsize=12, fontweight="bold")
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    out = CHARTS_DIR / "resilience_distribution.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"✓ {out}")


def chart_gradient(intensity_points: list) -> None:
    """Courbe : coût moyen vs intensité d'aléa, par doctrine."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.5))
    fig.suptitle(
        "§24.8.2 — Gradient de dégradation par intensité d'aléa",
        fontsize=13, fontweight="bold",
    )
    intensities = sorted({p.intensity_factor for p in intensity_points})
    for d in DOCTRINES:
        means = [
            next((p.cost_mean for p in intensity_points
                  if p.doctrine == d and p.intensity_factor == i), 0)
            for i in intensities
        ]
        p95s = [
            next((p.cost_p95 for p in intensity_points
                  if p.doctrine == d and p.intensity_factor == i), 0)
            for i in intensities
        ]
        ax1.plot(intensities, means, marker="o", linewidth=2,
                 color=COLORS[d], label=DOCTRINE_LABELS[d])
        ax2.plot(intensities, p95s, marker="s", linewidth=2,
                 color=COLORS[d], label=DOCTRINE_LABELS[d])

    for ax, title in [(ax1, "Coût moyen"), (ax2, "Coût P95 (tail risk)")]:
        ax.set_xlabel("Facteur d'intensité d'aléa")
        ax.set_ylabel("Coût (€)")
        ax.set_title(title)
        ax.legend(loc="upper left")
        ax.grid(alpha=0.3)
    plt.tight_layout()
    out = CHARTS_DIR / "resilience_gradient.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"✓ {out}")


def chart_cascade(cascade_points: list) -> None:
    """Courbe : coût + recovery vs N pannes simultanées."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.5))
    fig.suptitle(
        "§24.8.3 — Résistance aux défaillances en cascade",
        fontsize=13, fontweight="bold",
    )
    ns = sorted({p.n_breakdowns for p in cascade_points})
    for d in DOCTRINES:
        means = [
            next((p.cost_mean for p in cascade_points
                  if p.doctrine == d and p.n_breakdowns == n), 0)
            for n in ns
        ]
        recs = [
            next((p.recovery_days_mean for p in cascade_points
                  if p.doctrine == d and p.n_breakdowns == n), 0)
            for n in ns
        ]
        ax1.plot(ns, means, marker="o", linewidth=2,
                 color=COLORS[d], label=DOCTRINE_LABELS[d])
        ax2.plot(ns, recs, marker="^", linewidth=2,
                 color=COLORS[d], label=DOCTRINE_LABELS[d])

    ax1.set_xlabel("Nombre de pannes simultanées")
    ax1.set_ylabel("Coût moyen (€)")
    ax1.set_title("Coût en fonction du choc")
    ax1.legend(loc="upper left")
    ax1.grid(alpha=0.3)
    ax1.set_xticks(ns)

    ax2.set_xlabel("Nombre de pannes simultanées")
    ax2.set_ylabel("Temps de récupération (jours)")
    ax2.set_title("Time-to-recover (proxy MTTR)")
    ax2.legend(loc="upper left")
    ax2.grid(alpha=0.3)
    ax2.set_xticks(ns)

    plt.tight_layout()
    out = CHARTS_DIR / "resilience_cascade.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"✓ {out}")


def write_data_md(distributions: dict, intensity_points, cascade_points) -> None:
    """Écrit les tableaux markdown pour insertion dans §24.8."""
    lines: list[str] = []
    lines.append("# Données brutes — §24.8 Analyse de résilience")
    lines.append("")
    lines.append("## §24.8.1 — Distributions de coût (statistiques d'ordre)")
    lines.append("")
    lines.append("| Doctrine | N | Moyenne | σ | P50 | P75 | P95 | P99 | Max |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for d in DOCTRINES:
        dist = distributions[d]
        lines.append(
            f"| {DOCTRINE_LABELS[d]} | {dist.n_runs} "
            f"| {dist.mean:,.0f} € | {dist.std:,.0f} € "
            f"| {dist.p50:,.0f} | {dist.p75:,.0f} "
            f"| **{dist.p95:,.0f}** | **{dist.p99:,.0f}** "
            f"| {dist.max_val:,.0f} |".replace(",", " ")
        )
    lines.append("")
    lines.append("## §24.8.2 — Gradient d'intensité (coût moyen)")
    lines.append("")
    intensities = sorted({p.intensity_factor for p in intensity_points})
    header = "| Intensité | " + " | ".join(DOCTRINE_LABELS[d] for d in DOCTRINES) + " |"
    sep = "|---|" + "---|" * len(DOCTRINES)
    lines.append(header)
    lines.append(sep)
    for i in intensities:
        row = [f"{i:.1f}"]
        for d in DOCTRINES:
            p = next((p for p in intensity_points
                      if p.doctrine == d and p.intensity_factor == i), None)
            row.append(f"{p.cost_mean:,.0f} €".replace(",", " ") if p else "—")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    lines.append("## §24.8.3 — Cascade de pannes simultanées (coût + recovery)")
    lines.append("")
    lines.append("Coût moyen :")
    lines.append("")
    ns = sorted({p.n_breakdowns for p in cascade_points})
    lines.append(header.replace("Intensité", "Pannes"))
    lines.append(sep)
    for n in ns:
        row = [str(n)]
        for d in DOCTRINES:
            p = next((p for p in cascade_points
                      if p.doctrine == d and p.n_breakdowns == n), None)
            row.append(f"{p.cost_mean:,.0f} €".replace(",", " ") if p else "—")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    lines.append("Time-to-recover (jours) :")
    lines.append("")
    lines.append(header.replace("Intensité", "Pannes"))
    lines.append(sep)
    for n in ns:
        row = [str(n)]
        for d in DOCTRINES:
            p = next((p for p in cascade_points
                      if p.doctrine == d and p.n_breakdowns == n), None)
            row.append(f"{p.recovery_days_mean:.1f} j" if p else "—")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    DATA_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓ {DATA_MD}")


def main() -> None:
    print("=== Étude de résilience — §24.8 ===")
    print(f"Phase 1 — Distributions : "
          f"{len(DIST_FIXTURE_SEEDS) * len(DIST_SCEN_SEEDS) * len(DOCTRINES)} runs")
    print(f"Phase 2 — Gradient     : "
          f"{len(INTENSITY_LEVELS) * len(INTENSITY_SEEDS) * len(DOCTRINES)} runs")
    print(f"Phase 3 — Cascade      : "
          f"{len(CASCADE_LEVELS) * len(CASCADE_SEEDS) * len(DOCTRINES)} runs")

    with TemporaryDirectory(prefix="resilience_") as tmp:
        work = Path(tmp)
        # Phase 1 — distributions
        print("\n→ Phase 1 : grid distribution")
        raw = run_distribution_grid(
            fixture_seeds=DIST_FIXTURE_SEEDS,
            scenario_seeds=DIST_SCEN_SEEDS,
            doctrines=list(DOCTRINES),
            work_dir=work / "dist",
        )
        distributions = {
            d: compute_cost_distribution(d, raw[d]) for d in DOCTRINES
        }

        # Phase 2 — gradient (utilise 1 fixture set fixe pour isoler l'effet intensité)
        print("→ Phase 2 : gradient d'intensité")
        fix_dir = work / "fix_intensity"
        generate_random_fixtures(DEFAULT_SPEC, seed=42, out_dir=fix_dir)
        intensity_points = run_intensity_curve(
            intensities=INTENSITY_LEVELS,
            seeds=INTENSITY_SEEDS,
            doctrines=list(DOCTRINES),
            fixtures_dir=fix_dir,
            work_dir=work / "intensity",
        )

        # Phase 3 — cascade (idem 1 fixture set fixe)
        print("→ Phase 3 : cascade de pannes")
        cascade_points = run_cascade_curve(
            n_breakdowns_list=CASCADE_LEVELS,
            seeds=CASCADE_SEEDS,
            doctrines=list(DOCTRINES),
            fixtures_dir=fix_dir,
            work_dir=work / "cascade",
        )

    # Charts + données
    print("\n→ Génération des graphiques")
    chart_distribution(distributions)
    chart_gradient(intensity_points)
    chart_cascade(cascade_points)
    write_data_md(distributions, intensity_points, cascade_points)
    print("\nÉtude résilience terminée.")


if __name__ == "__main__":
    main()
