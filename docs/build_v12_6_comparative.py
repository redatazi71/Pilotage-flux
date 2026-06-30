"""V12.6 — Étude comparative V11 (smoothing libre) vs V12.6 (due-date aware).

Réutilise les 4 scénarios stress XL + override
`smoothing_due_date_aware` via le hook param_overrides de
run_doctrine.

Mesure : OTIF + coût pour FLUX et EVENT sous les 2 régimes.
Verdict attendu : V12.6 ramène OTIF proche d'OF+EVENT (~0.95) au
prix d'un coût légèrement supérieur (mais inférieur à OF pur).

Produit :
  - docs/charts/v12_6_otif_comparative.png
  - docs/cadrage_v4_v12_6_data.md
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
    DOCTRINE_EVENT,
    DOCTRINE_FLUX,
    baseline_xl_scenario,
    jitter_scenario,
    stress_cascade_nc_xl_scenario,
    stress_demand_spike_xl_scenario,
    stress_double_breakdown_xl_scenario,
)


HERE = Path(__file__).resolve().parent
CHARTS_DIR = HERE / "charts"
DATA_MD = HERE / "cadrage_v4_v12_6_data.md"

DOCTRINES_TESTED = [DOCTRINE_FLUX, DOCTRINE_EVENT]
DOCTRINE_LABELS = {DOCTRINE_FLUX: "FLUX", DOCTRINE_EVENT: "EVENT"}
COLORS = {DOCTRINE_FLUX: "#1f77b4", DOCTRINE_EVENT: "#2ca02c"}

SCENARIO_FACTORIES = {
    "baseline_xl": baseline_xl_scenario,
    "stress_double_breakdown_xl": stress_double_breakdown_xl_scenario,
    "stress_cascade_nc_xl": stress_cascade_nc_xl_scenario,
    "stress_demand_spike_xl": stress_demand_spike_xl_scenario,
}
SEEDS = list(range(3000, 3010))


def run_study() -> dict:
    """results[scenario][regime][doctrine] = {q, d, c, otif}."""
    total = (
        len(SCENARIO_FACTORIES) * 2 * len(SEEDS) * len(DOCTRINES_TESTED)
    )
    print(f"=== V12.6 — étude V11 vs V12.6 : {total} runs ===")
    results: dict = {}
    fixtures_dir = Path("data/fixtures_extended")

    with TemporaryDirectory(prefix="v12_6_") as tmp:
        work = Path(tmp)
        for scen_name, factory in SCENARIO_FACTORIES.items():
            print(f"\n→ {scen_name}")
            base = factory()
            results[scen_name] = {
                "v11": {d: {"q": [], "d": [], "c": [], "otif": []}
                        for d in DOCTRINES_TESTED},
                "v12_6": {d: {"q": [], "d": [], "c": [], "otif": []}
                          for d in DOCTRINES_TESTED},
            }
            for seed in SEEDS:
                scen = jitter_scenario(base, seed=seed)
                for d in DOCTRINES_TESTED:
                    # V11 : smoothing libre (défaut)
                    db_v11 = work / f"v11_{scen_name}_{seed}_{d}.db"
                    r11 = run_doctrine(
                        scen, d, db_v11, fixtures_dir=fixtures_dir,
                        evaluate_rejections=True,
                        late_threshold_days=3,
                    )
                    k11 = compute_kpis(scen, r11)
                    results[scen_name]["v11"][d]["q"].append(
                        k11.quantity_compliance,
                    )
                    results[scen_name]["v11"][d]["d"].append(
                        k11.disponibility_so_level,
                    )
                    results[scen_name]["v11"][d]["c"].append(
                        k11.total_cost_eur,
                    )
                    results[scen_name]["v11"][d]["otif"].append(
                        k11.quantity_compliance * k11.disponibility_so_level,
                    )

                    # V12.6 : smoothing due-date aware
                    db_v126 = work / f"v126_{scen_name}_{seed}_{d}.db"
                    r126 = run_doctrine(
                        scen, d, db_v126, fixtures_dir=fixtures_dir,
                        evaluate_rejections=True,
                        late_threshold_days=3,
                        param_overrides={
                            ("global", None, "smoothing_due_date_aware"): 1.0,
                        },
                    )
                    k126 = compute_kpis(scen, r126)
                    results[scen_name]["v12_6"][d]["q"].append(
                        k126.quantity_compliance,
                    )
                    results[scen_name]["v12_6"][d]["d"].append(
                        k126.disponibility_so_level,
                    )
                    results[scen_name]["v12_6"][d]["c"].append(
                        k126.total_cost_eur,
                    )
                    results[scen_name]["v12_6"][d]["otif"].append(
                        k126.quantity_compliance * k126.disponibility_so_level,
                    )
    return results


def chart_comparison(results: dict) -> None:
    """4 panneaux scénarios × 2 régimes : barres OTIF + coût."""
    fig, axes = plt.subplots(2, 2, figsize=(13, 10))
    fig.suptitle(
        "§28.12 — V12.6 due-date aware smoothing : OTIF V11 vs V12.6",
        fontsize=13, fontweight="bold",
    )
    axes_flat = axes.flatten()
    for idx, (scen, data) in enumerate(results.items()):
        ax = axes_flat[idx]
        labels = []
        otif_means = []
        colors = []
        for d in DOCTRINES_TESTED:
            for regime in ["v11", "v12_6"]:
                labels.append(f"{DOCTRINE_LABELS[d]}\n{regime}")
                otif_means.append(
                    statistics.mean(data[regime][d]["otif"]) * 100,
                )
                colors.append(COLORS[d])
        x = np.arange(len(labels))
        bars = ax.bar(x, otif_means, color=colors, edgecolor="black")
        # Hatcher V12.6 pour distinguer
        for i, bar in enumerate(bars):
            if "v12_6" in labels[i]:
                bar.set_hatch("//")
        ax.axhline(95, color="red", linestyle=":", alpha=0.6,
                   label="OTIF 95 %")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=9)
        ax.set_ylabel("OTIF (%)")
        ax.set_title(
            scen.replace("_xl", "").replace("_", " "),
            fontsize=11,
        )
        ax.set_ylim(0, 105)
        ax.grid(axis="y", alpha=0.3)
        for i, v in enumerate(otif_means):
            ax.text(i, v + 1, f"{v:.1f}", ha="center", fontsize=9,
                    fontweight="bold")
        if idx == 0:
            ax.legend(loc="lower right", fontsize=8)

    plt.tight_layout()
    out = CHARTS_DIR / "v12_6_otif_comparative.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"✓ {out}")


def write_data(results: dict) -> None:
    lines = ["# V12.6 — Comparaison V11 vs V12.6 due-date aware smoothing", ""]
    for scen, data in results.items():
        lines.append(f"## {scen}")
        lines.append("")
        lines.append(
            "| Doctrine | Régime | Q | D | OTIF | C (€) |"
        )
        lines.append("|---|---|---|---|---|---|")
        for d in DOCTRINES_TESTED:
            for regime in ["v11", "v12_6"]:
                q = statistics.mean(data[regime][d]["q"])
                dl = statistics.mean(data[regime][d]["d"])
                c = statistics.mean(data[regime][d]["c"])
                otif = q * dl
                lines.append(
                    f"| {DOCTRINE_LABELS[d]} | {regime} | "
                    f"{q:.3f} | {dl:.3f} | **{otif:.3f}** | "
                    f"{c:,.0f} €".replace(",", " ") + " |"
                )
        lines.append("")

    # Δ V12.6 vs V11
    lines.append("## Δ V12.6 vs V11 (gain OTIF, Δ coût)")
    lines.append("")
    lines.append(
        "| Scénario | Doctrine | Δ OTIF (pp) | Δ Coût (€) | Δ Coût (%) |"
    )
    lines.append("|---|---|---|---|---|")
    for scen, data in results.items():
        for d in DOCTRINES_TESTED:
            otif_v11 = statistics.mean(data["v11"][d]["otif"])
            otif_v126 = statistics.mean(data["v12_6"][d]["otif"])
            cost_v11 = statistics.mean(data["v11"][d]["c"])
            cost_v126 = statistics.mean(data["v12_6"][d]["c"])
            d_otif = (otif_v126 - otif_v11) * 100
            d_cost = cost_v126 - cost_v11
            d_cost_pct = d_cost / cost_v11 * 100 if cost_v11 else 0
            lines.append(
                f"| {scen} | {DOCTRINE_LABELS[d]} | "
                f"**{d_otif:+.1f} pp** | "
                f"{d_cost:+,.0f} €".replace(",", " ") + f" | "
                f"{d_cost_pct:+.1f} % |"
            )
    DATA_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓ {DATA_MD}")


def main() -> None:
    results = run_study()
    chart_comparison(results)
    write_data(results)
    print("\nV12.6 étude terminée.")


if __name__ == "__main__":
    main()
