"""Extension §24.8.5 + §24.10 — Point de rupture + Matrice paires de domaines.

  - §24.8.5 : cascade poussée à 6, 8, 10, 12, 15 pannes — knee point
  - §24.10  : matrice 5×5 paires (Appro, Logi, Qual, Prod, Dem)

Produit :
  - docs/charts/resilience_breaking_point.png
  - docs/charts/paired_hazards_heatmap.png
  - docs/charts/paired_hazards_recovery.png
  - docs/cadrage_v4_resilience_ext_data.md
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import matplotlib.pyplot as plt
import numpy as np

from pilotage_flux.comparative.resilience import (
    DOMAINS,
    run_cascade_curve,
    run_paired_matrix,
)
from pilotage_flux.comparative.scenario import DOCTRINES
from pilotage_flux.data_factory import DEFAULT_SPEC, generate_random_fixtures


HERE = Path(__file__).resolve().parent
CHARTS_DIR = HERE / "charts"
DATA_MD = HERE / "cadrage_v4_resilience_ext_data.md"

DOCTRINE_LABELS = {
    "of": "OF", "flux": "FLUX",
    "of_event": "OF+EVENT", "event": "EVENT",
}
COLORS = {
    "of": "#888888", "flux": "#1f77b4",
    "of_event": "#ff7f0e", "event": "#2ca02c",
}
DOMAIN_LABELS = {
    "appro": "Appro", "logi": "Logi", "qual": "Qual",
    "prod": "Prod", "dem": "Dem",
}

# Volumes (utilisateur a choisi version complète ~700 runs)
BREAKING_LEVELS = [6, 8, 10, 12, 15]
BREAKING_SEEDS = list(range(400, 420))   # 20 seeds × 5 levels × 4 = 400 runs
PAIR_SEEDS = list(range(500, 505))       # 5 seeds × (5 singles + 15 pairs) × 4 = 400 runs


def chart_breaking_point(cascade_points: list) -> None:
    """Courbe coût + disponibilité vs N pannes simultanées."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.5))
    fig.suptitle(
        "§24.8.5 — Point de rupture (cascade poussée à N=15)",
        fontsize=13, fontweight="bold",
    )
    ns = sorted({p.n_breakdowns for p in cascade_points})

    for d in DOCTRINES:
        costs = [
            next((p.cost_mean for p in cascade_points
                  if p.doctrine == d and p.n_breakdowns == n), 0)
            for n in ns
        ]
        avails = [
            next((p.availability_mean for p in cascade_points
                  if p.doctrine == d and p.n_breakdowns == n), 0)
            for n in ns
        ]
        ax1.plot(ns, costs, marker="o", linewidth=2,
                 color=COLORS[d], label=DOCTRINE_LABELS[d])
        ax2.plot(ns, [100 * a for a in avails], marker="s", linewidth=2,
                 color=COLORS[d], label=DOCTRINE_LABELS[d])

    ax1.set_xlabel("Nombre de pannes simultanées au jour 3")
    ax1.set_ylabel("Coût moyen (€)")
    ax1.set_title("Coût total — knee point")
    ax1.legend(loc="upper left")
    ax1.grid(alpha=0.3)
    ax1.set_xticks(ns)

    ax2.set_xlabel("Nombre de pannes simultanées au jour 3")
    ax2.set_ylabel("Disponibilité (% OF clôturés / créés)")
    ax2.set_title("Disponibilité — taux d'OF livrés")
    ax2.legend(loc="lower left")
    ax2.grid(alpha=0.3)
    ax2.set_xticks(ns)
    ax2.set_ylim(0, 105)

    plt.tight_layout()
    out = CHARTS_DIR / "resilience_breaking_point.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"✓ {out}")


def chart_paired_heatmap(paired_points: list, doctrine: str) -> None:
    """Heatmap 5×5 d'amplification de coût pour 1 doctrine."""
    matrix = np.full((len(DOMAINS), len(DOMAINS)), np.nan)
    for p in paired_points:
        if p.doctrine != doctrine:
            continue
        i = DOMAINS.index(p.domain_a)
        j = DOMAINS.index(p.domain_b)
        matrix[i, j] = p.amplification
        matrix[j, i] = p.amplification  # symétrie

    return matrix


def chart_all_paired_heatmaps(paired_points: list) -> None:
    """Une heatmap par doctrine, en grille 2×2."""
    fig, axes = plt.subplots(2, 2, figsize=(13, 11))
    fig.suptitle(
        "§24.10 — Matrice de paires de domaines : amplification de coût\n"
        "(2 aléas simultanés vs moyenne des 2 aléas isolés ; >1 = sur-coût)",
        fontsize=13, fontweight="bold",
    )
    axes_flat = axes.flatten()
    for idx, d in enumerate(DOCTRINES):
        ax = axes_flat[idx]
        m = chart_paired_heatmap(paired_points, d)
        im = ax.imshow(m, cmap="RdYlGn_r", vmin=0.8, vmax=2.0)
        ax.set_xticks(range(len(DOMAINS)))
        ax.set_yticks(range(len(DOMAINS)))
        ax.set_xticklabels([DOMAIN_LABELS[x] for x in DOMAINS])
        ax.set_yticklabels([DOMAIN_LABELS[y] for y in DOMAINS])
        ax.set_title(f"Doctrine {DOCTRINE_LABELS[d]}", fontweight="bold")
        # Annoter les cellules
        for i in range(len(DOMAINS)):
            for j in range(len(DOMAINS)):
                v = m[i, j]
                if not np.isnan(v):
                    color = "white" if v > 1.4 else "black"
                    ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                            color=color, fontsize=9, fontweight="bold")
        plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

    plt.tight_layout()
    out = CHARTS_DIR / "paired_hazards_heatmap.png"
    fig.savefig(out, dpi=110, bbox_inches="tight")
    plt.close(fig)
    print(f"✓ {out}")


def chart_paired_recovery(recovery_per_pair: dict) -> None:
    """Heatmap 5×5 du time-to-recover par paire, pour la doctrine EVENT
    (la plus résiliente — montre quelle paire est la plus longue)."""
    fig, axes = plt.subplots(2, 2, figsize=(13, 11))
    fig.suptitle(
        "§24.10 — Time-to-recover par paire de domaines (jours)",
        fontsize=13, fontweight="bold",
    )
    axes_flat = axes.flatten()
    for idx, d in enumerate(DOCTRINES):
        ax = axes_flat[idx]
        matrix = np.full((len(DOMAINS), len(DOMAINS)), np.nan)
        for key, val in recovery_per_pair[d].items():
            dom_a, dom_b = key.split("_")
            i = DOMAINS.index(dom_a)
            j = DOMAINS.index(dom_b)
            matrix[i, j] = val
            matrix[j, i] = val
        im = ax.imshow(matrix, cmap="Reds", vmin=0, vmax=10)
        ax.set_xticks(range(len(DOMAINS)))
        ax.set_yticks(range(len(DOMAINS)))
        ax.set_xticklabels([DOMAIN_LABELS[x] for x in DOMAINS])
        ax.set_yticklabels([DOMAIN_LABELS[y] for y in DOMAINS])
        ax.set_title(f"Doctrine {DOCTRINE_LABELS[d]}", fontweight="bold")
        for i in range(len(DOMAINS)):
            for j in range(len(DOMAINS)):
                v = matrix[i, j]
                if not np.isnan(v):
                    color = "white" if v > 6 else "black"
                    ax.text(j, i, f"{v:.1f}", ha="center", va="center",
                            color=color, fontsize=9, fontweight="bold")
        plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label="jours")

    plt.tight_layout()
    out = CHARTS_DIR / "paired_hazards_recovery.png"
    fig.savefig(out, dpi=110, bbox_inches="tight")
    plt.close(fig)
    print(f"✓ {out}")


def write_data_md(cascade_points: list, paired_points: list,
                    recovery_per_pair: dict) -> None:
    lines: list[str] = []
    lines.append("# Données brutes §24.8.5 + §24.10")
    lines.append("")

    # Breaking point
    lines.append("## §24.8.5 — Point de rupture")
    lines.append("")
    lines.append("| N pannes | OF coût | FLUX coût | OF+EVENT coût | EVENT coût | OF dispo | FLUX dispo | OF+EVENT dispo | EVENT dispo |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    ns = sorted({p.n_breakdowns for p in cascade_points})
    for n in ns:
        row = [f"{n}"]
        for d in DOCTRINES:
            p = next((x for x in cascade_points
                      if x.doctrine == d and x.n_breakdowns == n), None)
            row.append(f"{p.cost_mean:,.0f} €".replace(",", " ") if p else "—")
        for d in DOCTRINES:
            p = next((x for x in cascade_points
                      if x.doctrine == d and x.n_breakdowns == n), None)
            row.append(f"{100*p.availability_mean:.1f} %" if p else "—")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    # Paired matrix per doctrine
    lines.append("## §24.10 — Matrice paires : amplification de coût")
    lines.append("")
    for d in DOCTRINES:
        lines.append(f"### Doctrine {DOCTRINE_LABELS[d]}")
        lines.append("")
        header = "| | " + " | ".join(DOMAIN_LABELS[x] for x in DOMAINS) + " |"
        sep = "|---|" + "---|" * len(DOMAINS)
        lines.append(header)
        lines.append(sep)
        for dom_a in DOMAINS:
            row = [DOMAIN_LABELS[dom_a]]
            for dom_b in DOMAINS:
                p = next((x for x in paired_points
                          if x.doctrine == d
                          and {x.domain_a, x.domain_b} == {dom_a, dom_b}),
                         None)
                row.append(f"{p.amplification:.2f}" if p else "—")
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    # Recovery per pair
    lines.append("## §24.10 — Time-to-recover par paire (jours)")
    lines.append("")
    for d in DOCTRINES:
        lines.append(f"### Doctrine {DOCTRINE_LABELS[d]}")
        lines.append("")
        lines.append(header)
        lines.append(sep)
        for dom_a in DOMAINS:
            row = [DOMAIN_LABELS[dom_a]]
            for dom_b in DOMAINS:
                key1 = f"{dom_a}_{dom_b}"
                key2 = f"{dom_b}_{dom_a}"
                val = recovery_per_pair[d].get(key1, recovery_per_pair[d].get(key2))
                row.append(f"{val:.1f}" if val is not None else "—")
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    DATA_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓ {DATA_MD}")


def main() -> None:
    print("=== Extension résilience — §24.8.5 + §24.10 ===")
    n_breaking = len(BREAKING_LEVELS) * len(BREAKING_SEEDS) * len(DOCTRINES)
    n_paired = (len(DOMAINS) + 15) * len(PAIR_SEEDS) * len(DOCTRINES)
    print(f"Phase 1 — Point de rupture : {n_breaking} runs")
    print(f"Phase 2 — Matrice paires    : {n_paired} runs (5 singles + 15 paires)")
    print(f"Total                       : {n_breaking + n_paired} runs")

    with TemporaryDirectory(prefix="resilience_ext_") as tmp:
        work = Path(tmp)
        fix_dir = work / "fix"
        generate_random_fixtures(DEFAULT_SPEC, seed=42, out_dir=fix_dir)

        print("\n→ Phase 1 : point de rupture")
        cascade_points = run_cascade_curve(
            n_breakdowns_list=BREAKING_LEVELS,
            seeds=BREAKING_SEEDS,
            doctrines=list(DOCTRINES),
            fixtures_dir=fix_dir,
            work_dir=work / "breaking",
        )

        print("→ Phase 2 : matrice paires")
        paired_points, recovery_per_pair = run_paired_matrix(
            seeds=PAIR_SEEDS,
            doctrines=list(DOCTRINES),
            fixtures_dir=fix_dir,
            work_dir=work / "pairs",
        )

    print("\n→ Génération des graphiques")
    chart_breaking_point(cascade_points)
    chart_all_paired_heatmaps(paired_points)
    chart_paired_recovery(recovery_per_pair)
    write_data_md(cascade_points, paired_points, recovery_per_pair)
    print("\nExtension terminée.")


if __name__ == "__main__":
    main()
