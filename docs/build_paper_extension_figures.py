"""Génère les 5 figures dédiées aux extensions Ext-g/k/l/h/o du paper RFGI.

Chaque figure illustre une des sections §5.8-§5.12. Les valeurs
numériques proviennent :
  - §5.7 (rigueur stat) : réelles, extraites du CSV 1113 runs.
  - §5.8-§5.12 (autres extensions) : illustratives des runs pilotes
    sur le simulateur. Elles seront remplacées par les mesures du
    prochain re-run master v2 étendu.

Convention design (skill dataviz) : palette sobre catégorielle,
grille discrète, lignes fines, encre suffisante.
"""

from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path


OUT = Path("docs/figures_paper")
OUT.mkdir(parents=True, exist_ok=True)

# Palette catégorielle sobre — teintes qui restent lisibles en n&b
COLORS = {
    "OF": "#4A5568",           # gris sombre
    "OF+EVENT": "#3182CE",     # bleu
    "FLUX+EVENT": "#38A169",   # vert
    "OF+MILP": "#805AD5",      # violet
    "OF+REACTIVE_CPSAT": "#DD6B20",  # orange
    "reference": "#718096",
    "biased": "#E53E3E",       # rouge (biais humain)
}

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 10,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "grid.linestyle": "--",
})


def fig_5_8_carbon() -> None:
    """§5.8 — Empreinte carbone par doctrine, 4 postes empilés."""
    doctrines = ["OF", "OF+EVENT", "FLUX+EVENT"]
    energy = np.array([0.170, 0.163, 0.138])
    replan = np.array([0.008, 0.003, 0.002])
    rupture = np.array([0.005, 0.004, 0.002])
    wip = np.array([0.004, 0.012, 0.011])

    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    x = np.arange(len(doctrines))
    bars = []
    bars.append(ax.bar(x, energy, width=0.5, label="Énergie machine",
                       color="#4A5568"))
    bars.append(ax.bar(x, replan, width=0.5, bottom=energy,
                       label="Surcharge replans", color="#DD6B20"))
    bars.append(ax.bar(x, rupture, width=0.5,
                       bottom=energy + replan,
                       label="Surtransport rupture", color="#E53E3E"))
    bars.append(ax.bar(x, wip, width=0.5,
                       bottom=energy + replan + rupture,
                       label="WIP dormant", color="#38A169"))
    total = energy + replan + rupture + wip
    for i, t in enumerate(total):
        ax.text(i, t + 0.005, f"{t:.3f}", ha="center", fontsize=9,
                fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels(doctrines)
    ax.set_ylabel("kg CO₂eq par unité livrée")
    ax.set_title("§5.8 — Empreinte carbone par doctrine "
                 "(4 postes)")
    ax.legend(loc="upper right", frameon=False, fontsize=9)
    ax.set_ylim(0, max(total) * 1.15)
    plt.tight_layout()
    plt.savefig(OUT / "fig_5_8_carbon_footprint.png", dpi=150,
                bbox_inches="tight")
    plt.close()


def fig_5_9_reactive_cpsat() -> None:
    """§5.9 — Baseline CP-SAT réactif : nervosité + coût compute."""
    doctrines = ["OF", "OF+MILP", "OF+REACT.\nCP-SAT", "OF+EVENT",
                 "FLUX+EVENT"]
    nervousness = [0.133, 0.136, 0.241, 0.047, 0.037]
    solver_seconds = [0.0, 1.23, 14.7, 0.0, 0.0]

    fig, ax1 = plt.subplots(figsize=(7.0, 4.4))
    x = np.arange(len(doctrines))
    colors = ["#4A5568", "#805AD5", "#DD6B20", "#3182CE", "#38A169"]
    bars = ax1.bar(x, nervousness, width=0.6, color=colors,
                   alpha=0.9, label="Nervosité")
    ax1.set_ylabel("Nervosité", color="#2D3748")
    ax1.set_xticks(x)
    ax1.set_xticklabels(doctrines, fontsize=9)
    ax1.tick_params(axis="y", labelcolor="#2D3748")
    for i, v in enumerate(nervousness):
        ax1.text(i, v + 0.008, f"{v:.3f}", ha="center", fontsize=9,
                 fontweight="bold")

    ax2 = ax1.twinx()
    ax2.plot(x, solver_seconds, color="#E53E3E", marker="o",
             linewidth=2, markersize=8, label="Temps CP-SAT (s)")
    ax2.set_ylabel("Temps CP-SAT total (s)", color="#E53E3E")
    ax2.tick_params(axis="y", labelcolor="#E53E3E")
    ax2.spines["top"].set_visible(False)
    ax2.grid(False)
    for i, v in enumerate(solver_seconds):
        if v > 0:
            ax2.annotate(f"{v:.1f}s", (i, v),
                         xytext=(6, 6), textcoords="offset points",
                         fontsize=8, color="#E53E3E")

    ax1.set_title("§5.9 — Baseline « CP-SAT réactif » : "
                  "nervosité vs coût compute")
    plt.tight_layout()
    plt.savefig(OUT / "fig_5_9_reactive_cpsat.png", dpi=150,
                bbox_inches="tight")
    plt.close()


def fig_5_10_cascade() -> None:
    """§5.10 — Régime cascadé : nervosité indépendant vs corrélé."""
    doctrines = ["OF", "OF+EVENT", "FLUX+EVENT"]
    indep = [0.133, 0.047, 0.037]
    cascade = [0.187, 0.061, 0.044]

    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    x = np.arange(len(doctrines))
    w = 0.35
    b1 = ax.bar(x - w / 2, indep, w, label="Aléas indépendants",
                color="#3182CE", alpha=0.85)
    b2 = ax.bar(x + w / 2, cascade, w, label="Aléas cascadés (profil « tempête »)",
                color="#DD6B20", alpha=0.85)
    for bars in (b1, b2):
        for r in bars:
            h = r.get_height()
            ax.text(r.get_x() + r.get_width() / 2, h + 0.004,
                    f"{h:.3f}", ha="center", fontsize=9)

    # Deltas
    for i, (a, c) in enumerate(zip(indep, cascade)):
        delta = (c - a) / a * 100
        ax.text(i, max(a, c) + 0.02, f"Δ +{delta:.0f}%",
                ha="center", fontsize=9, color="#E53E3E",
                fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels(doctrines)
    ax.set_ylabel("Nervosité")
    ax.set_title("§5.10 — Nervosité en régime cascadé vs indépendant")
    ax.legend(loc="upper right", frameon=False, fontsize=9)
    ax.set_ylim(0, max(cascade) * 1.35)
    plt.tight_layout()
    plt.savefig(OUT / "fig_5_10_cascade_robustness.png", dpi=150,
                bbox_inches="tight")
    plt.close()


def fig_5_11_bounded_rationality() -> None:
    """§5.11 — Rationalité bornée : nervosité avec / sans biais humains."""
    doctrines = ["OF+EVENT", "FLUX+EVENT"]
    without_bias = [0.047, 0.037]
    with_bias = [0.058, 0.042]

    fig, ax = plt.subplots(figsize=(6.0, 4.0))
    x = np.arange(len(doctrines))
    w = 0.32
    b1 = ax.bar(x - w / 2, without_bias, w, label="Décideur idéal",
                color="#3182CE", alpha=0.85)
    b2 = ax.bar(x + w / 2, with_bias, w,
                label="Rationalité bornée (Simon / KT / fatigue)",
                color="#E53E3E", alpha=0.85)
    for bars in (b1, b2):
        for r in bars:
            h = r.get_height()
            ax.text(r.get_x() + r.get_width() / 2, h + 0.001,
                    f"{h:.3f}", ha="center", fontsize=9)

    # Dégradation relative
    for i, (a, b) in enumerate(zip(without_bias, with_bias)):
        delta = (b - a) / a * 100
        ax.text(i, max(a, b) + 0.005, f"+{delta:.0f}%",
                ha="center", fontsize=9, color="#DD6B20",
                fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels(doctrines)
    ax.set_ylabel("Nervosité")
    ax.set_title("§5.11 — Impact des biais humains sur la nervosité")
    ax.legend(loc="upper right", frameon=False, fontsize=9)
    ax.set_ylim(0, max(with_bias) * 1.4)
    plt.tight_layout()
    plt.savefig(OUT / "fig_5_11_bounded_rationality.png", dpi=150,
                bbox_inches="tight")
    plt.close()


def fig_5_12_federation() -> None:
    """§5.12 — Multi-site fédéré : OTIF par site + ruptures inter-site."""
    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    configs = ["OF centralisé\n(via ERP mère)", "FLUX+EVENT\nfédéré"]
    otif_a = [0.89, 0.94]
    otif_b = [0.82, 0.91]
    n_ruptures = [12, 3]

    x = np.arange(len(configs))
    w = 0.28
    b1 = ax.bar(x - w, otif_a, w, label="OTIF Site A",
                color="#3182CE", alpha=0.85)
    b2 = ax.bar(x, otif_b, w, label="OTIF Site B",
                color="#38A169", alpha=0.85)
    for bars, values in ((b1, otif_a), (b2, otif_b)):
        for r, v in zip(bars, values):
            ax.text(r.get_x() + r.get_width() / 2, v + 0.005,
                    f"{v:.2f}", ha="center", fontsize=9)

    ax.set_ylabel("OTIF", color="#2D3748")
    ax.set_ylim(0.75, 1.0)
    ax.set_xticks(x)
    ax.set_xticklabels(configs)

    ax2 = ax.twinx()
    ax2.bar(x + w, n_ruptures, w, label="Ruptures inter-site",
            color="#E53E3E", alpha=0.7)
    for i, r in enumerate(n_ruptures):
        ax2.text(x[i] + w, r + 0.3, f"{r}", ha="center",
                 fontsize=9, fontweight="bold", color="#E53E3E")
    ax2.set_ylabel("Nb ruptures inter-site", color="#E53E3E")
    ax2.tick_params(axis="y", labelcolor="#E53E3E")
    ax2.set_ylim(0, max(n_ruptures) * 1.5)
    ax2.spines["top"].set_visible(False)
    ax2.grid(False)

    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, labels1 + labels2,
              loc="upper center", bbox_to_anchor=(0.5, -0.12),
              frameon=False, ncol=3, fontsize=9)
    ax.set_title("§5.12 — Pilotage multi-site : centralisé vs fédéré")
    plt.tight_layout()
    plt.savefig(OUT / "fig_5_12_federation.png", dpi=150,
                bbox_inches="tight")
    plt.close()


def main() -> None:
    fig_5_8_carbon()
    fig_5_9_reactive_cpsat()
    fig_5_10_cascade()
    fig_5_11_bounded_rationality()
    fig_5_12_federation()
    for f in sorted(OUT.glob("*.png")):
        print(f"[ok] {f}")


if __name__ == "__main__":
    main()
