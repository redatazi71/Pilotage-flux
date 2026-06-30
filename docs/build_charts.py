"""Génère les graphiques PNG du cadrage v4 (décomposition 2×2 + per-scenario).

Usage : python docs/build_charts.py

Produit :
  - docs/charts/decomposition_2x2.png  : barres Δ vs OF sur les 2 protocoles
  - docs/charts/per_scenario_xl.png    : barres Δ vs OF par scénario XL
  - docs/charts/lead_time_comparison.png : lead time par doctrine, 2 protocoles
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


HERE = Path(__file__).resolve().parent
CHARTS_DIR = HERE / "charts"
CHARTS_DIR.mkdir(parents=True, exist_ok=True)


# Données XL — §24.2 (4 000 runs : 5 scénarios × 4 doctrines × 200 seeds)
XL_DATA = {
    "scenarios": [
        "baseline_xl", "stress_double_breakdown_xl",
        "stress_cascade_nc_xl", "stress_demand_spike_xl",
        "stress_multi_contract_overload",
    ],
    "of":       [45067, 48586, 34125, 47173, 21391],
    "flux":     [32098, 30251, 27845, 41680,  9942],
    "of_event": [39274, 35890, 34056, 47173, 20104],
    "event":    [32098, 27590, 27718, 41680,  9942],
}

# Données Random — §24.6 (1 600 runs : 20 fixtures × 20 scénarios × 4 doctrines)
RANDOM_DATA = {
    "of":       201774,
    "flux":     162481,
    "of_event": 196134,
    "event":    157780,
}
RANDOM_LEAD = {
    "of":       (8.61, 1.27),
    "flux":     (4.80, 0.94),
    "of_event": (8.52, 1.25),
    "event":    (4.69, 0.94),
}

XL_LEAD = {
    # moyennes des 5 scenarios
    "of":       8.07,  # ~ (8.65+8.81+8.22+10.19+4.5)/5
    "flux":     4.56,
    "of_event": 7.90,
    "event":    4.54,
}

DOCTRINE_LABELS = {
    "of":       "OF\n(V0)",
    "flux":     "FLUX\n(V1+V2)",
    "of_event": "OF+EVENT\n(V0+ES)",
    "event":    "EVENT\n(combiné)",
}
COLORS = {
    "of":       "#888888",
    "flux":     "#1f77b4",
    "of_event": "#ff7f0e",
    "event":    "#2ca02c",
}


# ---------------------------------------------------------------------------
# 1) Décomposition 2×2 — Δ vs OF (XL + Random)
# ---------------------------------------------------------------------------

def chart_decomposition_2x2() -> None:
    """Bars : Δ vs OF par doctrine, 2 sous-graphes (XL puis Random)."""
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    fig.suptitle("Décomposition 2×2 — Δ coût (€) vs OF",
                 fontsize=14, fontweight="bold")

    # Panel XL : moyenne agrégée sur 5 scénarios
    of_xl_avg = sum(XL_DATA["of"]) / len(XL_DATA["of"])
    deltas_xl = {
        d: (sum(XL_DATA[d]) / len(XL_DATA[d])) - of_xl_avg
        for d in ("flux", "of_event", "event")
    }
    axes[0].bar(
        [DOCTRINE_LABELS[d] for d in ("flux", "of_event", "event")],
        [deltas_xl[d] for d in ("flux", "of_event", "event")],
        color=[COLORS[d] for d in ("flux", "of_event", "event")],
        edgecolor="black",
    )
    axes[0].axhline(0, color="black", linewidth=0.8)
    axes[0].set_title("§24.2 — 4 000 runs XL (5 scénarios × 200 seeds)\n"
                      "Fixtures industrielle fixe",
                      fontsize=11)
    axes[0].set_ylabel("Δ coût moyen vs OF (€)")
    for i, d in enumerate(("flux", "of_event", "event")):
        axes[0].text(i, deltas_xl[d] - 200, f"{deltas_xl[d]:+,.0f} €",
                     ha="center", va="top", fontweight="bold")

    # Panel Random
    of_rd = RANDOM_DATA["of"]
    deltas_rd = {d: RANDOM_DATA[d] - of_rd for d in ("flux", "of_event", "event")}
    axes[1].bar(
        [DOCTRINE_LABELS[d] for d in ("flux", "of_event", "event")],
        [deltas_rd[d] for d in ("flux", "of_event", "event")],
        color=[COLORS[d] for d in ("flux", "of_event", "event")],
        edgecolor="black",
    )
    axes[1].axhline(0, color="black", linewidth=0.8)
    axes[1].set_title("§24.6 — 1 600 runs Random (20 fixtures × 20 scénarios)\n"
                      "Configurations industrielles aléatoires",
                      fontsize=11)
    axes[1].set_ylabel("Δ coût total vs OF (€)")
    for i, d in enumerate(("flux", "of_event", "event")):
        axes[1].text(i, deltas_rd[d] - 800, f"{deltas_rd[d]:+,.0f} €",
                     ha="center", va="top", fontweight="bold")

    plt.tight_layout()
    out = CHARTS_DIR / "decomposition_2x2.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"✓ {out}")


# ---------------------------------------------------------------------------
# 2) Per-scenario XL — Δ coût par doctrine, 5 scénarios
# ---------------------------------------------------------------------------

def chart_per_scenario_xl() -> None:
    """Barres groupées : pour chaque scénario, Δ par doctrine."""
    fig, ax = plt.subplots(figsize=(13, 6))
    scens = XL_DATA["scenarios"]
    of_costs = np.array(XL_DATA["of"])
    width = 0.25
    x = np.arange(len(scens))

    for i, d in enumerate(("flux", "of_event", "event")):
        deltas = np.array(XL_DATA[d]) - of_costs
        bars = ax.bar(x + (i - 1) * width, deltas, width,
                      label=DOCTRINE_LABELS[d].replace("\n", " "),
                      color=COLORS[d], edgecolor="black")
        for bar in bars:
            h = bar.get_height()
            ax.text(bar.get_x() + bar.get_width() / 2, h - 300,
                    f"{int(h):+,}", ha="center", va="top",
                    fontsize=8, fontweight="bold")

    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels([s.replace("_", "\n", 1) for s in scens],
                       fontsize=9, rotation=10)
    ax.set_ylabel("Δ coût (€) vs OF")
    ax.set_title("§24.2 — Δ coût par scénario × doctrine (4 000 runs XL)",
                 fontsize=12, fontweight="bold")
    ax.legend(loc="lower right")
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    out = CHARTS_DIR / "per_scenario_xl.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"✓ {out}")


# ---------------------------------------------------------------------------
# 3) Lead time comparison — XL vs Random
# ---------------------------------------------------------------------------

def chart_lead_time() -> None:
    """Lead time par doctrine sur les 2 protocoles."""
    fig, ax = plt.subplots(figsize=(10, 5.5))
    doctrines = ["of", "flux", "of_event", "event"]
    x = np.arange(len(doctrines))
    width = 0.36

    xl_vals = [XL_LEAD[d] for d in doctrines]
    rd_vals = [RANDOM_LEAD[d][0] for d in doctrines]
    rd_errs = [RANDOM_LEAD[d][1] for d in doctrines]

    ax.bar(x - width / 2, xl_vals, width,
           label="§24.2 — XL (4 000 runs)", color="#4c72b0", edgecolor="black")
    ax.bar(x + width / 2, rd_vals, width, yerr=rd_errs,
           label="§24.6 — Random (1 600 runs)", color="#dd8452",
           edgecolor="black", capsize=4)

    for i, v in enumerate(xl_vals):
        ax.text(i - width / 2, v + 0.15, f"{v:.2f}j",
                ha="center", va="bottom", fontsize=9, fontweight="bold")
    for i, v in enumerate(rd_vals):
        ax.text(i + width / 2, v + 0.15, f"{v:.2f}j",
                ha="center", va="bottom", fontsize=9, fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels([DOCTRINE_LABELS[d].replace("\n", " ") for d in doctrines],
                       fontsize=10)
    ax.set_ylabel("Lead time moyen (jours)")
    ax.set_title("Lead time par doctrine — convergence des 2 protocoles",
                 fontsize=12, fontweight="bold")
    ax.legend(loc="upper right")
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    out = CHARTS_DIR / "lead_time_comparison.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"✓ {out}")


# ---------------------------------------------------------------------------
# 4) Additivité — barres empilées sur Random (1 600 runs)
# ---------------------------------------------------------------------------

def chart_additivity() -> None:
    """Additivité quasi-parfaite : flux seul + event seul ≈ combiné."""
    fig, ax = plt.subplots(figsize=(8, 5))
    cats = ["Sommé naïvement\n(flux seul + event seul)", "Réalisé\n(combiné)"]
    flux_part = 39293
    event_part = 5640
    sommed = flux_part + event_part
    realized = 43994

    ax.bar([0], [flux_part], width=0.5, color=COLORS["flux"],
           edgecolor="black", label="Apport flux seul")
    ax.bar([0], [event_part], width=0.5, bottom=[flux_part],
           color=COLORS["of_event"], edgecolor="black",
           label="Apport event seul")
    ax.bar([1], [realized], width=0.5, color=COLORS["event"],
           edgecolor="black", label="Apport combiné mesuré")

    ax.text(0, sommed + 800, f"{sommed:,} €",
            ha="center", va="bottom", fontweight="bold")
    ax.text(1, realized + 800, f"{realized:,} €",
            ha="center", va="bottom", fontweight="bold")
    # Interaction
    delta = sommed - realized
    ax.annotate(
        f"Sub-additivité : {delta} € ({100 * delta / sommed:.1f} %)",
        xy=(0.5, sommed), xytext=(0.5, sommed + 5000),
        ha="center", fontsize=10,
        arrowprops=dict(arrowstyle="->", color="black"),
    )

    ax.set_xticks([0, 1])
    ax.set_xticklabels(cats, fontsize=10)
    ax.set_ylabel("Apport (€) — abs(Δ vs OF)")
    ax.set_title("§24.6.4 — Additivité quasi-parfaite des 2 apports (Random)",
                 fontsize=12, fontweight="bold")
    ax.legend(loc="upper right")
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    out = CHARTS_DIR / "additivity.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"✓ {out}")


if __name__ == "__main__":
    chart_decomposition_2x2()
    chart_per_scenario_xl()
    chart_lead_time()
    chart_additivity()
    print("\nTous les graphiques générés dans docs/charts/")
