"""Génère docs/charts/paper_fig5_zones_architecture.png — V12 zones."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as patches


HERE = Path(__file__).resolve().parent
CHARTS_DIR = HERE / "charts"


def main() -> None:
    fig, ax = plt.subplots(figsize=(14, 7))
    ax.set_xlim(0, 50)
    ax.set_ylim(0, 10)
    ax.axis("off")

    # Axe temporel
    ax.annotate("", xy=(48, 5), xytext=(2, 5),
                arrowprops=dict(arrowstyle="->", color="black", lw=2))
    ax.text(48, 5.4, "Temps (jours)", ha="right", fontsize=10,
            fontweight="bold")
    ax.plot([2, 2], [4.8, 5.2], color="red", lw=3)
    ax.text(2, 4.4, "t=maintenant", ha="center", fontsize=9, color="red")

    # Zone gelée (rouge)
    freeze = patches.FancyBboxPatch(
        (2, 6), 6, 1.5,
        boxstyle="round,pad=0.05",
        linewidth=2, edgecolor="#c00000", facecolor="#ffcccc",
    )
    ax.add_patch(freeze)
    ax.text(5, 6.75, "ZONE GELÉE", ha="center", va="center",
            fontsize=11, fontweight="bold", color="#c00000")
    ax.text(5, 5.5, "freeze_window\n[t, t+5j[", ha="center", va="center",
            fontsize=8, color="#c00000")

    # Zone négociable (bleu)
    nego = patches.FancyBboxPatch(
        (8, 6), 22, 1.5,
        boxstyle="round,pad=0.05",
        linewidth=2, edgecolor="#0070c0", facecolor="#cce5ff",
    )
    ax.add_patch(nego)
    ax.text(19, 6.75, "ZONE NÉGOCIABLE", ha="center", va="center",
            fontsize=12, fontweight="bold", color="#0070c0")
    ax.text(19, 5.5, "horizon_forecast\n[t+5j, t+28j[", ha="center",
            va="center", fontsize=8, color="#0070c0")

    # Zone libre (vert)
    libre = patches.FancyBboxPatch(
        (30, 6), 18, 1.5,
        boxstyle="round,pad=0.05",
        linewidth=2, edgecolor="#00802b", facecolor="#ccffcc",
    )
    ax.add_patch(libre)
    ax.text(39, 6.75, "ZONE LIBRE", ha="center", va="center",
            fontsize=11, fontweight="bold", color="#00802b")
    ax.text(39, 5.5, "long terme\n[t+28j, ∞[", ha="center", va="center",
            fontsize=8, color="#00802b")

    # Niveaux V12 utilisés
    # V12.2 sur zone négociable
    v122 = patches.FancyBboxPatch(
        (8, 2.5), 22, 1.5,
        boxstyle="round,pad=0.05",
        linewidth=2, edgecolor="#0070c0", facecolor="#e6f2ff",
    )
    ax.add_patch(v122)
    ax.text(19, 3.4, "V12.2 — CP-SAT dynamique", ha="center", va="center",
            fontsize=11, fontweight="bold", color="#0070c0")
    ax.text(19, 2.7, "+ heuristiques SLACK/EDD/SPT/ATC",
            ha="center", va="center", fontsize=9, color="#0070c0",
            fontstyle="italic")

    # V12.1 sur zone libre
    v121 = patches.FancyBboxPatch(
        (30, 2.5), 18, 1.5,
        boxstyle="round,pad=0.05",
        linewidth=2, edgecolor="#00802b", facecolor="#e6ffe6",
    )
    ax.add_patch(v121)
    ax.text(39, 3.4, "V12.1 — Forecasting", ha="center", va="center",
            fontsize=11, fontweight="bold", color="#00802b")
    ax.text(39, 2.7, "ARIMA + Holt-Winters + Regression + Ensemble",
            ha="center", va="center", fontsize=8, color="#00802b",
            fontstyle="italic")

    # Zone gelée → V12.3 Delta engine surveille mais ne propose pas
    v123 = patches.FancyBboxPatch(
        (2, 2.5), 6, 1.5,
        boxstyle="round,pad=0.05",
        linewidth=2, edgecolor="#c00000", facecolor="#ffe6e6",
    )
    ax.add_patch(v123)
    ax.text(5, 3.4, "V12.3 surveille", ha="center", va="center",
            fontsize=10, fontweight="bold", color="#c00000")
    ax.text(5, 2.7, "(pas de replan)", ha="center", va="center",
            fontsize=8, color="#c00000", fontstyle="italic")

    # V12.3 Delta engine traverse les 3 zones par-dessus
    ax.annotate("", xy=(48, 0.8), xytext=(2, 0.8),
                arrowprops=dict(arrowstyle="->", color="purple",
                                  lw=2.5, alpha=0.7))
    ax.text(25, 1.2, "V12.3 Delta engine — détection + matrice 4 niveaux L1/L2/L3/L4",
            ha="center", va="center", fontsize=10, color="purple",
            fontweight="bold")

    # Titre
    ax.text(25, 9.2,
            "Figure 5 — V12 architecture cybernétique : "
            "3 zones temporelles × couches algorithmiques",
            ha="center", va="center", fontsize=13, fontweight="bold")

    plt.tight_layout()
    out = CHARTS_DIR / "paper_fig5_zones_architecture.png"
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"✓ {out}")


if __name__ == "__main__":
    main()
