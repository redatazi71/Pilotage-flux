"""Génère docs/charts/paper_fig6_v12_complete.png — V12 architecture finale."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.patches import FancyArrowPatch


HERE = Path(__file__).resolve().parent
CHARTS_DIR = HERE / "charts"


def main() -> None:
    fig, ax = plt.subplots(figsize=(15, 9))
    ax.set_xlim(0, 50)
    ax.set_ylim(0, 14)
    ax.axis("off")

    # Titre
    ax.text(25, 13.4,
            "Figure 6 — V12 architecture cybernétique complète (6 briques livrées)",
            ha="center", va="center", fontsize=14, fontweight="bold")

    # V12.5 Matrice d'orchestration (couche du dessus, transversale)
    matrix_box = patches.FancyBboxPatch(
        (2, 10.5), 46, 1.6,
        boxstyle="round,pad=0.1",
        linewidth=2.5, edgecolor="#5B2C6F",
        facecolor="#E8DAEF",
    )
    ax.add_patch(matrix_box)
    ax.text(25, 11.6, "V12.5 — Matrice d'orchestration",
            ha="center", va="center", fontsize=12, fontweight="bold",
            color="#5B2C6F")
    ax.text(25, 10.9,
            "WorkshopProfile (JSON) + OrchestrationContext + select_optimizer / select_forecaster",
            ha="center", va="center", fontsize=9, color="#5B2C6F",
            fontstyle="italic")

    # V12.3 Delta engine (4 niveaux d'autonomie)
    delta_box = patches.FancyBboxPatch(
        (2, 7.5), 46, 2.4,
        boxstyle="round,pad=0.1",
        linewidth=2, edgecolor="#5D4E60",
        facecolor="#F0EFE9",
    )
    ax.add_patch(delta_box)
    ax.text(25, 9.5, "V12.3 — Delta engine (4 niveaux d'autonomie)",
            ha="center", va="center", fontsize=11, fontweight="bold",
            color="#5D4E60")
    # 4 niveaux côte à côte
    levels = [
        ("L1\nabsorbé", "#A9DFBF"),
        ("L2\nauto", "#F9E79F"),
        ("L3\nopérateur", "#F5B7B1"),
        ("L4\nsupervisor", "#D7BDE2"),
    ]
    x0 = 4
    for label, color in levels:
        b = patches.FancyBboxPatch(
            (x0, 7.8), 10.5, 1.2,
            boxstyle="round,pad=0.05",
            linewidth=1.5, edgecolor="black", facecolor=color,
        )
        ax.add_patch(b)
        ax.text(x0 + 5.25, 8.4, label, ha="center", va="center",
                fontsize=10, fontweight="bold")
        x0 += 11.0

    # 3 zones temporelles : libre, négociable, gelée
    # Zone gelée
    freeze_box = patches.FancyBboxPatch(
        (2, 4.5), 13, 2.4,
        boxstyle="round,pad=0.1",
        linewidth=2, edgecolor="#C00000", facecolor="#F5B7B1",
    )
    ax.add_patch(freeze_box)
    ax.text(8.5, 6.6, "Zone gelée", ha="center", va="center",
            fontsize=11, fontweight="bold", color="#C00000")
    ax.text(8.5, 5.9, "[t, t+5j[", ha="center", va="center",
            fontsize=9, color="#C00000")
    ax.text(8.5, 5.2, "Pas de modification\n(audit only)",
            ha="center", va="center", fontsize=8, color="#3B0000",
            fontstyle="italic")

    # Zone négociable
    nego_box = patches.FancyBboxPatch(
        (16, 4.5), 18, 2.4,
        boxstyle="round,pad=0.1",
        linewidth=2, edgecolor="#0070C0", facecolor="#AED6F1",
    )
    ax.add_patch(nego_box)
    ax.text(25, 6.6, "Zone négociable", ha="center", va="center",
            fontsize=11, fontweight="bold", color="#0070C0")
    ax.text(25, 5.9, "[t+5j, t+28j[", ha="center", va="center",
            fontsize=9, color="#0070C0")
    ax.text(25, 5.2,
            "V12.2 CP-SAT dynamique\n+ V12.2.1 feedback",
            ha="center", va="center", fontsize=9, color="#0E4F73",
            fontweight="bold")

    # Zone libre
    libre_box = patches.FancyBboxPatch(
        (35, 4.5), 13, 2.4,
        boxstyle="round,pad=0.1",
        linewidth=2, edgecolor="#00802B", facecolor="#A9DFBF",
    )
    ax.add_patch(libre_box)
    ax.text(41.5, 6.6, "Zone libre", ha="center", va="center",
            fontsize=11, fontweight="bold", color="#00802B")
    ax.text(41.5, 5.9, "[t+28j, ∞[", ha="center", va="center",
            fontsize=9, color="#00802B")
    ax.text(41.5, 5.2,
            "V12.1 forecasting\n+ V12.1.1 feedback",
            ha="center", va="center", fontsize=9, color="#0E4F2B",
            fontweight="bold")

    # V12.4 Human loop — couche transversale en dessous
    human_box = patches.FancyBboxPatch(
        (2, 1.5), 46, 2.6,
        boxstyle="round,pad=0.1",
        linewidth=2, edgecolor="#943126", facecolor="#FADBD8",
    )
    ax.add_patch(human_box)
    ax.text(25, 3.7, "V12.4 — Workflow humain complet",
            ha="center", va="center", fontsize=12, fontweight="bold",
            color="#943126")
    # 4 sous-modules
    submodules = [
        ("roles.py\n(operator/supervisor/admin)", 7),
        ("audit_log.py\n(7 event types)", 19),
        ("escalation.py\n(L3 → L4 auto)", 31),
        ("dashboard.py\n(snapshot 24h)", 43),
    ]
    for label, x in submodules:
        b = patches.FancyBboxPatch(
            (x - 4.5, 1.8), 9.5, 1.5,
            boxstyle="round,pad=0.05",
            linewidth=1, edgecolor="#943126", facecolor="white",
        )
        ax.add_patch(b)
        ax.text(x, 2.55, label, ha="center", va="center",
                fontsize=8, color="#943126")

    # Flèches verticales : V12.5 pilote tout
    for x in [10, 25, 40]:
        arrow = FancyArrowPatch(
            (x, 10.5), (x, 7.0),
            arrowstyle="->,head_length=8,head_width=6",
            color="#5B2C6F", linewidth=1.5, alpha=0.7,
        )
        ax.add_patch(arrow)
    # Flèches vers human loop
    for x in [10, 25, 40]:
        arrow = FancyArrowPatch(
            (x, 4.5), (x, 4.15),
            arrowstyle="->,head_length=6,head_width=5",
            color="#943126", linewidth=1.5, alpha=0.7,
        )
        ax.add_patch(arrow)

    # Légende compteurs
    ax.text(25, 0.7,
            "6 briques V12 livrées · 107 tests verts · 0 régression V11",
            ha="center", va="center", fontsize=11, fontweight="bold",
            color="#1A5276")

    plt.tight_layout()
    out = CHARTS_DIR / "paper_fig6_v12_complete.png"
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"✓ {out}")


if __name__ == "__main__":
    main()
