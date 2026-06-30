"""V12.8 — Étude comparative V11 vs V12.7 (magic) vs V12.8 (principled).

Compare 3 régimes sur les 4 scénarios stress XL :
  - V11             : smoothing libre (défaut)
  - V12.7 sf=150    : safety_factor empirique (équivaut à désactiver
                      le smoothing pour OFs courts)
  - V12.8 CPM+BOM   : CPM data-driven + BOM topological sort

Verdict attendu : V12.8 principled ne suffit pas à recouvrer V12.7
sur ces scénarios — l'« art noir » du facteur 150 cache un fait
doctrinal : sur des BOMs avec dépendances et capacité limitée,
le smoothing dégrade l'OTIF. La leçon V12.8 est de quantifier l'écart.

Produit :
  - docs/charts/v12_8_otif_comparative.png
  - docs/cadrage_v4_v12_8_data.md
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
DATA_MD = HERE / "cadrage_v4_v12_8_data.md"

DOCTRINES = [DOCTRINE_FLUX, DOCTRINE_EVENT]
LABELS = {DOCTRINE_FLUX: "FLUX", DOCTRINE_EVENT: "EVENT"}
COLORS = {DOCTRINE_FLUX: "#1f77b4", DOCTRINE_EVENT: "#2ca02c"}

SCENS = {
    "baseline_xl": baseline_xl_scenario,
    "stress_double_breakdown_xl": stress_double_breakdown_xl_scenario,
    "stress_cascade_nc_xl": stress_cascade_nc_xl_scenario,
    "stress_demand_spike_xl": stress_demand_spike_xl_scenario,
}
SEEDS = list(range(3000, 3010))

REGIMES = {
    "v11": {},
    "v12_7": {
        ("global", None, "smoothing_horizon_aware"): 1.0,
        ("global", None, "smoothing_horizon_safety_factor"): 150.0,
    },
    "v12_8": {
        ("global", None, "smoothing_cpm_aware"): 1.0,
        ("global", None, "smoothing_bom_topo"): 1.0,
    },
}


def run_study() -> dict:
    n = len(SCENS) * len(REGIMES) * len(SEEDS) * len(DOCTRINES)
    print(f"=== V12.8 — V11 vs V12.7 vs V12.8 : {n} runs ===")
    results: dict = {}
    fixtures = Path("data/fixtures_extended")
    with TemporaryDirectory(prefix="v128cmp_") as tmp:
        work = Path(tmp)
        for sn, fact in SCENS.items():
            print(f"\n→ {sn}")
            base = fact()
            results[sn] = {
                rn: {d: {"q": [], "d": [], "c": [], "otif": []}
                     for d in DOCTRINES}
                for rn in REGIMES
            }
            for seed in SEEDS:
                scen = jitter_scenario(base, seed=seed)
                for d in DOCTRINES:
                    for rn, ovs in REGIMES.items():
                        db = work / f"{rn}_{sn}_{seed}_{d}.db"
                        r = run_doctrine(
                            scen, d, db, fixtures_dir=fixtures,
                            evaluate_rejections=True,
                            late_threshold_days=3,
                            param_overrides=ovs if ovs else None,
                        )
                        k = compute_kpis(scen, r)
                        bucket = results[sn][rn][d]
                        bucket["q"].append(k.quantity_compliance)
                        bucket["d"].append(k.disponibility_so_level)
                        bucket["c"].append(k.total_cost_eur)
                        bucket["otif"].append(
                            k.quantity_compliance * k.disponibility_so_level
                        )
    return results


def chart(results: dict) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(13, 10))
    fig.suptitle(
        "§28.14 — V12.8 principled vs V12.7 magic : OTIF moyenne",
        fontsize=13, fontweight="bold",
    )
    axes_flat = axes.flatten()
    for idx, (sn, data) in enumerate(results.items()):
        ax = axes_flat[idx]
        labels, otifs, colors = [], [], []
        for d in DOCTRINES:
            for rn in ("v11", "v12_7", "v12_8"):
                labels.append(f"{LABELS[d]}\n{rn}")
                otifs.append(statistics.mean(data[rn][d]["otif"]) * 100)
                colors.append(COLORS[d])
        x = np.arange(len(labels))
        bars = ax.bar(x, otifs, color=colors, edgecolor="black")
        for i, b in enumerate(bars):
            if "v12_7" in labels[i]:
                b.set_hatch("//")
            elif "v12_8" in labels[i]:
                b.set_hatch("xx")
        ax.axhline(95, color="red", linestyle=":", alpha=0.6,
                   label="OTIF 95 %")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=8)
        ax.set_ylabel("OTIF (%)")
        ax.set_title(sn.replace("_xl", "").replace("_", " "), fontsize=11)
        ax.set_ylim(0, 105)
        ax.grid(axis="y", alpha=0.3)
        for i, v in enumerate(otifs):
            ax.text(i, v + 1, f"{v:.0f}", ha="center", fontsize=8,
                    fontweight="bold")
        if idx == 0:
            ax.legend(loc="lower right", fontsize=8)
    plt.tight_layout()
    out = CHARTS_DIR / "v12_8_otif_comparative.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"✓ {out}")


def write_md(results: dict) -> None:
    lines = ["# V12.8 — V11 vs V12.7 magic vs V12.8 principled", ""]
    for sn, data in results.items():
        lines.append(f"## {sn}")
        lines.append("")
        lines.append("| Doctrine | Régime | Q | D | OTIF | C (€) |")
        lines.append("|---|---|---|---|---|---|")
        for d in DOCTRINES:
            for rn in ("v11", "v12_7", "v12_8"):
                b = data[rn][d]
                q = statistics.mean(b["q"])
                dl = statistics.mean(b["d"])
                c = statistics.mean(b["c"])
                otif = q * dl
                lines.append(
                    f"| {LABELS[d]} | {rn} | {q:.3f} | {dl:.3f} | "
                    f"**{otif:.3f}** | "
                    f"{c:,.0f} €".replace(",", " ") + " |"
                )
        lines.append("")
    lines.append("## Δ OTIF (pp) par régime vs V11")
    lines.append("")
    lines.append("| Scénario | Doctrine | V12.7 (pp) | V12.8 (pp) |")
    lines.append("|---|---|---|---|")
    for sn, data in results.items():
        for d in DOCTRINES:
            v11 = statistics.mean(data["v11"][d]["otif"])
            v127 = statistics.mean(data["v12_7"][d]["otif"])
            v128 = statistics.mean(data["v12_8"][d]["otif"])
            lines.append(
                f"| {sn} | {LABELS[d]} | "
                f"**{(v127 - v11) * 100:+.1f}** | "
                f"**{(v128 - v11) * 100:+.1f}** |"
            )
    DATA_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓ {DATA_MD}")


def main() -> None:
    results = run_study()
    chart(results)
    write_md(results)
    print("\nV12.8 étude terminée.")


if __name__ == "__main__":
    main()
