"""§30 — Étude OTIF-first sur 4 scénarios stress XL.

OTIF (On-Time-In-Full) = livraison ontime ET en pleine quantité
          = Q (quantity_compliance) × D (disponibility_so_level)

Protocole : 4 scénarios stress × 4 doctrines × 10 seeds = 160 runs.

Ranking hiérarchique du planificateur :
  1. OTIF en premier (priorité absolue)
  2. Coût en second (à OTIF égal, choisir le moins cher)

Produit :
  - docs/charts/otif_first_4_scenarios.png
  - docs/cadrage_v4_otif_data.md
"""

from __future__ import annotations

import statistics
from pathlib import Path
from tempfile import TemporaryDirectory

import matplotlib.pyplot as plt
import numpy as np

from pilotage_flux.comparative.kpis import compute_kpis
from pilotage_flux.comparative.runner import run_doctrine
from pilotage_flux.comparative.scenario import (
    DOCTRINES,
    baseline_xl_scenario,
    jitter_scenario,
    stress_cascade_nc_xl_scenario,
    stress_demand_spike_xl_scenario,
    stress_double_breakdown_xl_scenario,
)


HERE = Path(__file__).resolve().parent
CHARTS_DIR = HERE / "charts"
DATA_MD = HERE / "cadrage_v4_otif_data.md"

DOCTRINE_LABELS = {
    "of": "OF", "flux": "FLUX",
    "of_event": "OF+EVENT", "event": "EVENT",
}
COLORS = {
    "of": "#888888", "flux": "#1f77b4",
    "of_event": "#ff7f0e", "event": "#2ca02c",
}

SCENARIO_FACTORIES = {
    "baseline_xl": baseline_xl_scenario,
    "stress_double_breakdown_xl": stress_double_breakdown_xl_scenario,
    "stress_cascade_nc_xl": stress_cascade_nc_xl_scenario,
    "stress_demand_spike_xl": stress_demand_spike_xl_scenario,
}
SEEDS = list(range(2000, 2010))  # 10 seeds


def run_study() -> dict:
    """results[scenario][doctrine] = {q, d, c, otif} lists."""
    total = len(SCENARIO_FACTORIES) * len(SEEDS) * len(DOCTRINES)
    print(f"=== OTIF-first — {total} runs ===")
    results = {
        s: {d: {"q": [], "d": [], "c": [], "otif": []} for d in DOCTRINES}
        for s in SCENARIO_FACTORIES
    }

    fixtures_dir = Path("data/fixtures_extended")
    with TemporaryDirectory(prefix="otif_") as tmp:
        work = Path(tmp)
        for scen_name, factory in SCENARIO_FACTORIES.items():
            print(f"\n→ Scénario {scen_name}")
            base = factory()
            for seed in SEEDS:
                scen = jitter_scenario(base, seed=seed)
                for d in DOCTRINES:
                    db_path = work / f"otif_{scen_name}_{seed}_{d}.db"
                    result = run_doctrine(
                        scen, d, db_path,
                        fixtures_dir=fixtures_dir,
                        evaluate_rejections=True,
                        late_threshold_days=3,
                    )
                    kpi = compute_kpis(scen, result)
                    q = kpi.quantity_compliance
                    delivery = kpi.disponibility_so_level
                    otif = q * delivery
                    results[scen_name][d]["q"].append(q)
                    results[scen_name][d]["d"].append(delivery)
                    results[scen_name][d]["c"].append(kpi.total_cost_eur)
                    results[scen_name][d]["otif"].append(otif)
    return results


def rank_otif_first(
    results: dict, otif_threshold: float = 0.90,
) -> dict[str, list[tuple[str, float, float]]]:
    """Pour chaque scénario, ranking hiérarchique :
      1. Filtre OTIF ≥ threshold
      2. Parmi survivants, trie par coût croissant

    Returns: {scenario: [(doctrine, otif_mean, cost_mean), ...]}
    """
    ranking = {}
    for scen, data in results.items():
        candidates = []
        for d, kpis in data.items():
            otif_mean = statistics.mean(kpis["otif"])
            cost_mean = statistics.mean(kpis["c"])
            candidates.append((d, otif_mean, cost_mean))
        # Filtre OTIF ≥ threshold
        passing = [c for c in candidates if c[1] >= otif_threshold]
        # Trie par coût croissant
        if passing:
            ranking[scen] = sorted(passing, key=lambda x: x[2])
        else:
            # Aucune doctrine ne passe → meilleur OTIF malgré le seuil
            ranking[scen] = sorted(candidates, key=lambda x: -x[1])
    return ranking


def chart_otif_first(results: dict, ranking: dict) -> None:
    """4 panneaux (un par scénario) : OTIF × Coût scatter."""
    fig, axes = plt.subplots(2, 2, figsize=(13, 10))
    fig.suptitle(
        "§30 — Ranking OTIF-first → Coût-second sur 4 scénarios stress (160 runs)",
        fontsize=13, fontweight="bold",
    )
    axes_flat = axes.flatten()
    for idx, (scen, data) in enumerate(results.items()):
        ax = axes_flat[idx]
        for d in DOCTRINES:
            otif = statistics.mean(data[d]["otif"])
            cost = statistics.mean(data[d]["c"])
            ax.scatter(otif * 100, cost, s=200, color=COLORS[d],
                       edgecolor="black", linewidth=1.5,
                       label=f"{DOCTRINE_LABELS[d]} (OTIF={otif*100:.1f}%, "
                              f"C={cost:,.0f}€)".replace(",", " "),
                       zorder=10)
        # Seuil OTIF = 90 %
        ax.axvline(90, color="red", linestyle=":", alpha=0.5,
                    label="Seuil OTIF 90 %")
        # Marqueur du choix recommandé (premier du ranking)
        if scen in ranking and ranking[scen]:
            best_d, best_otif, best_cost = ranking[scen][0]
            ax.scatter(best_otif * 100, best_cost, s=400,
                       facecolor="none", edgecolor="gold",
                       linewidth=3, zorder=11,
                       label=f"⭐ Choix : {DOCTRINE_LABELS[best_d]}")
        ax.set_xlabel("OTIF = Q × D (%)")
        ax.set_ylabel("Coût moyen (€)")
        ax.set_title(scen.replace("_xl", "").replace("_", " "), fontsize=11)
        ax.grid(alpha=0.3)
        ax.legend(loc="upper right", fontsize=7)
        ax.set_xlim(60, 105)

    plt.tight_layout()
    out = CHARTS_DIR / "otif_first_4_scenarios.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"✓ {out}")


def write_data(results: dict) -> None:
    lines = ["# §30 — Étude OTIF-first sur 4 scénarios stress", ""]
    for scen, data in results.items():
        lines.append(f"## {scen}")
        lines.append("")
        lines.append(
            "| Doctrine | Q (compliance) | D (dispo) | "
            "OTIF = Q×D | C (coût €) |"
        )
        lines.append("|---|---|---|---|---|")
        for d in DOCTRINES:
            q = statistics.mean(data[d]["q"])
            dl = statistics.mean(data[d]["d"])
            c = statistics.mean(data[d]["c"])
            otif = q * dl
            lines.append(
                f"| {DOCTRINE_LABELS[d]} | {q:.3f} | {dl:.3f} | "
                f"**{otif:.3f}** | {c:,.0f} €".replace(",", " ") + " |"
            )
        lines.append("")

    # Ranking OTIF-first à 3 seuils
    for threshold in (0.95, 0.90, 0.80):
        lines.append(f"## Ranking OTIF-first (seuil = {threshold:.0%})")
        lines.append("")
        ranking = rank_otif_first(results, otif_threshold=threshold)
        lines.append(
            "| Scénario | Choix | OTIF | Coût | Alternative 2 |"
        )
        lines.append("|---|---|---|---|---|")
        for scen, ranked in ranking.items():
            if ranked:
                first = ranked[0]
                second = ranked[1] if len(ranked) > 1 else None
                second_str = (
                    f"{DOCTRINE_LABELS[second[0]]} (OTIF {second[1]:.2f}, "
                    f"C {second[2]:,.0f} €)".replace(",", " ")
                    if second else "—"
                )
                lines.append(
                    f"| {scen} | "
                    f"**{DOCTRINE_LABELS[first[0]]}** | "
                    f"{first[1]:.3f} | "
                    f"{first[2]:,.0f} €".replace(",", " ") + f" | "
                    f"{second_str} |"
                )
        lines.append("")

    DATA_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓ {DATA_MD}")


def main() -> None:
    results = run_study()
    ranking = rank_otif_first(results, otif_threshold=0.90)
    chart_otif_first(results, ranking)
    write_data(results)
    print("\nÉtude OTIF-first terminée.")


if __name__ == "__main__":
    main()
