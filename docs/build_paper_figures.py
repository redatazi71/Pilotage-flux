"""Génère les figures spécifiques au paper HAL (Fig 1 architecture,
Fig 2 synthèse exécutive radar).

Usage : python docs/build_paper_figures.py
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.patches import FancyArrowPatch
import numpy as np


HERE = Path(__file__).resolve().parent
CHARTS_DIR = HERE / "charts"
CHARTS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Figure 1 — Architecture en 3 piliers
# ---------------------------------------------------------------------------

def figure_architecture() -> None:
    fig, ax = plt.subplots(figsize=(12, 7))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 8)
    ax.axis("off")

    pillars = [
        ("Pilier FLUX\ncontractualisé",
         "flux/contracts.py\nflux/smoothing.py\nflux/freeze.py\nflux/coherence.py",
         "absorbe l'amplitude",
         "#1f77b4", 1.0),
        ("Pilier EVENT\nsourcing",
         "events_v3/expected.py\nevents_v3/matching.py\nevents_v3/dual_tolerance.py\nevents_v3/dual_memory.py\nlearning.py",
         "absorbe l'interaction",
         "#2ca02c", 5.0),
        ("Pilier P3 collective\n+ tampons Little",
         "gates/p3_collective.py\nflux/buffers.py\nidentify_bottlenecks",
         "limite le sur-engagement",
         "#ff7f0e", 9.0),
    ]
    for label, modules, role, color, x in pillars:
        # Boîte pilier
        box = patches.FancyBboxPatch(
            (x - 0.2, 4.0), 2.8, 3.0,
            boxstyle="round,pad=0.1",
            linewidth=2, edgecolor=color, facecolor="white",
        )
        ax.add_patch(box)
        ax.text(x + 1.2, 6.5, label, ha="center", va="center",
                fontsize=12, fontweight="bold", color=color)
        ax.text(x + 1.2, 5.4, modules, ha="center", va="center",
                fontsize=8, family="monospace", color="black")
        ax.text(x + 1.2, 4.3, role, ha="center", va="center",
                fontsize=9, fontstyle="italic", color="#444444")

    # Bandeau APS+MES en bas
    base = patches.FancyBboxPatch(
        (0.4, 1.5), 11.2, 1.5,
        boxstyle="round,pad=0.1",
        linewidth=1.5, edgecolor="#444444",
        facecolor="#f0f0f0",
    )
    ax.add_patch(base)
    ax.text(6.0, 2.5, "APS + MES (ISA-95 niveaux 4-3-2)",
            ha="center", va="center", fontsize=13, fontweight="bold")
    ax.text(6.0, 2.0, "SQLite event store immuable · 15 modules · 299 tests · 48 commandes CLI",
            ha="center", va="center", fontsize=10, color="#444444")

    # Titre
    ax.text(6, 7.7, "Figure 1 — Architecture doctrinale en 3 piliers",
            ha="center", va="center", fontsize=14, fontweight="bold")

    # Flèches vers le bandeau
    for x in [2.2, 6.2, 10.2]:
        arrow = FancyArrowPatch(
            (x, 4.0), (x, 3.0),
            arrowstyle="->,head_length=8,head_width=6",
            color="#666666", linewidth=1.5,
        )
        ax.add_patch(arrow)

    out = CHARTS_DIR / "paper_fig1_architecture.png"
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"✓ {out}")


# ---------------------------------------------------------------------------
# Figure 2 — Radar synthèse exécutive 5 dimensions × 4 doctrines
# ---------------------------------------------------------------------------

def figure_radar() -> None:
    fig, ax = plt.subplots(figsize=(10, 9),
                            subplot_kw=dict(projection="polar"))

    # 5 dimensions, normalisées 0-1 où 1 = meilleur
    dimensions = [
        "Coût absolu\n(−% vs OF)",
        "Lead time\n(×OF / valeur)",
        "Nervosité\n(÷ vs OF)",
        "MTTR\n(% OF)",
        "Résistance\ncascade",
    ]
    # Valeurs normalisées : 0 = OF référence, 1 = meilleure performance théorique
    # Coût : 0 % = ratio 1.0 (no gain) ; -22 % = ratio 0.78 ; 1 = ratio 0.78
    # Lead time : OF 8.61j, EVENT 4.69j → ratio 1.83
    # Nervosité : OF 0.350, EVENT 0.090 → ÷3.9
    # MTTR : OF 5.7j → 2.8j EVENT
    # Sensibilité cascade : +23.2 % OF → +4.5 % EVENT
    doctrines = {
        "OF": [0.00, 0.00, 0.00, 0.00, 0.00],
        "FLUX":     [0.89, 0.84, 0.00, 0.65, 0.25],
        "OF+EVENT": [0.13, 0.04, 1.00, 0.05, 0.62],
        "EVENT":    [1.00, 0.87, 1.00, 1.00, 1.00],
    }
    colors = {
        "OF": "#888888", "FLUX": "#1f77b4",
        "OF+EVENT": "#ff7f0e", "EVENT": "#2ca02c",
    }

    n = len(dimensions)
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
    angles += angles[:1]

    for d, vals in doctrines.items():
        v = vals + vals[:1]
        ax.plot(angles, v, marker="o", linewidth=2,
                color=colors[d], label=d)
        ax.fill(angles, v, alpha=0.10, color=colors[d])

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(dimensions, fontsize=10)
    ax.set_ylim(0, 1.05)
    ax.set_yticks([0.25, 0.5, 0.75, 1.0])
    ax.set_yticklabels(["25 %", "50 %", "75 %", "100 %"], fontsize=8)
    ax.set_rlabel_position(135)
    ax.grid(True, alpha=0.4)

    ax.set_title(
        "Figure 2 — Synthèse exécutive : 5 dimensions × 4 doctrines\n"
        "(1.0 = meilleur score observé, 0.0 = doctrine OF de référence)",
        fontsize=12, fontweight="bold", pad=20,
    )
    ax.legend(loc="upper right", bbox_to_anchor=(1.30, 1.10),
              fontsize=10, frameon=True)

    plt.tight_layout()
    out = CHARTS_DIR / "paper_fig2_radar_synthesis.png"
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"✓ {out}")


# ---------------------------------------------------------------------------
# Figure 3 — Mapping 5 domaines de perturbation → mécanismes simulateur
# ---------------------------------------------------------------------------

def figure_domain_mapping() -> None:
    fig, ax = plt.subplots(figsize=(13, 7))
    ax.set_xlim(0, 13)
    ax.set_ylim(0, 7)
    ax.axis("off")

    domains = [
        ("Approvisionnement", "#1f77b4",
         "HAZARD_PO_DELAY",
         "Décale expected_at\nd'un PO d'achat"),
        ("Logistique", "#ff7f0e",
         "HAZARD_LOGISTIC_DELAY",
         "Bloque un poste\nde travail N jours"),
        ("Qualité", "#2ca02c",
         "HAZARD_QUALITY_NC",
         "Scrap d'une quantité\nde stock interne"),
        ("Production", "#d62728",
         "HAZARD_BREAKDOWN",
         "Slowdown factor\nsur un poste"),
        ("Demande", "#9467bd",
         "HAZARD_URGENT_ORDER",
         "Création SO urgente\navec due date courte"),
    ]

    n = len(domains)
    spacing = 13.0 / n
    for idx, (label, color, hazard, mechanism) in enumerate(domains):
        x = spacing * (idx + 0.5)

        # Domaine (haut)
        box1 = patches.FancyBboxPatch(
            (x - 1.0, 5.2), 2.0, 1.3,
            boxstyle="round,pad=0.05",
            linewidth=2, edgecolor=color, facecolor="white",
        )
        ax.add_patch(box1)
        ax.text(x, 5.85, label, ha="center", va="center",
                fontsize=11, fontweight="bold", color=color)

        # Flèche
        arrow = FancyArrowPatch(
            (x, 5.2), (x, 4.3),
            arrowstyle="->,head_length=8,head_width=6",
            color=color, linewidth=2,
        )
        ax.add_patch(arrow)

        # Mécanisme (bas)
        box2 = patches.FancyBboxPatch(
            (x - 1.1, 2.5), 2.2, 1.8,
            boxstyle="round,pad=0.05",
            linewidth=1.5, edgecolor=color, facecolor="#f8f8f8",
        )
        ax.add_patch(box2)
        ax.text(x, 3.85, hazard, ha="center", va="center",
                fontsize=9, family="monospace", color=color,
                fontweight="bold")
        ax.text(x, 3.0, mechanism, ha="center", va="center",
                fontsize=8.5, color="#222222")

    # Titre
    ax.text(6.5, 6.7,
            "Figure 3 — Mapping des 5 domaines de perturbation "
            "aux mécanismes du simulateur",
            ha="center", va="center",
            fontsize=13, fontweight="bold")

    # Bandeau bas
    base = patches.FancyBboxPatch(
        (0.4, 0.6), 12.2, 1.3,
        boxstyle="round,pad=0.1",
        linewidth=1.5, edgecolor="#444444",
        facecolor="#f0f0f0",
    )
    ax.add_patch(base)
    ax.text(6.5, 1.55,
            "Couverture taxonomique des 5 domaines de la matrice §5.4 — 5 mécanismes distincts, "
            "tous appliqués au jour 3 du scénario",
            ha="center", va="center", fontsize=10)
    ax.text(6.5, 1.0,
            "HAZARD_LOGISTIC_DELAY est l'extension §24.9 du modèle "
            "initial (4 hazards) pour couvrir le domaine logistique",
            ha="center", va="center", fontsize=9, fontstyle="italic",
            color="#555555")

    plt.tight_layout()
    out = CHARTS_DIR / "paper_fig3_domain_mapping.png"
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"✓ {out}")


if __name__ == "__main__":
    figure_architecture()
    figure_radar()
    figure_domain_mapping()
    print("\n3 figures paper générées dans docs/charts/")
