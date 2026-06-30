"""Matrice QCDS V13.1 — BOM-op linkage + V13.0 + réaliste cap=480.

100 runs : 5 doctrines × 4 scénarios × 5 seeds.

Configurations testées :
  - OF baseline (référence)
  - FLUX legacy (V11)
  - EVENT V11 (= FLUX, mêmes offsets)
  - EVENT V13.0 (event-driven smoothing reactivity)
  - EVENT V13.0 + V13.1 (+ BOM-op linkage)
  - EVENT V13.0 + V13.1 + réaliste (combo complet)
"""

from __future__ import annotations

import statistics
from pathlib import Path
from tempfile import TemporaryDirectory

from pilotage_flux.comparative.kpis import compute_kpis
from pilotage_flux.comparative.runner import run_doctrine
from pilotage_flux.comparative.scenario import (
    DOCTRINE_EVENT, DOCTRINE_FLUX, DOCTRINE_OF,
    baseline_xl_scenario,
    jitter_scenario,
    stress_cascade_nc_xl_scenario,
    stress_demand_spike_xl_scenario,
    stress_double_breakdown_xl_scenario,
)


HERE = Path(__file__).resolve().parent
DATA_MD = HERE / "cadrage_v4_qcds_v13_1.md"


def measure(scen, result) -> dict:
    k = compute_kpis(scen, result)
    wips = list(result.daily_wip.values())
    wip_peak = max(wips) if wips else 0
    wip_sd = statistics.stdev(wips) if len(wips) > 1 else 0.0
    return {
        "Q": k.quantity_compliance,
        "D": k.disponibility_so_level,
        "OTIF": k.quantity_compliance * k.disponibility_so_level,
        "C": k.total_cost_eur,
        "wip_peak": wip_peak,
        "wip_sd": wip_sd,
    }


def main() -> None:
    fixtures = Path("data/fixtures_extended")
    scens = {
        "baseline_xl": baseline_xl_scenario,
        "stress_double_breakdown_xl": stress_double_breakdown_xl_scenario,
        "stress_cascade_nc_xl": stress_cascade_nc_xl_scenario,
        "stress_demand_spike_xl": stress_demand_spike_xl_scenario,
    }
    cfgs = [
        ("OF", DOCTRINE_OF, None),
        ("FLUX V11", DOCTRINE_FLUX, None),
        ("EVENT V13.0", DOCTRINE_EVENT, {
            ("global", None, "event_driven_smoothing_advance_days"): 5.0,
        }),
        ("EVENT V13.0 + V13.1", DOCTRINE_EVENT, {
            ("global", None, "event_driven_smoothing_advance_days"): 5.0,
            ("global", None, "bom_op_linkage_aware"): 1.0,
        }),
        ("EVENT V13.0+V13.1+réal", DOCTRINE_EVENT, {
            ("global", None, "event_driven_smoothing_advance_days"): 5.0,
            ("global", None, "bom_op_linkage_aware"): 1.0,
            ("global", None, "realistic_capacity_minutes_per_day"): 480.0,
        }),
    ]
    seeds = list(range(3000, 3005))
    n = len(scens) * len(cfgs) * len(seeds)
    print(f"=== QCDS V13.1 : {n} runs ===\n")

    results = {sn: {label: [] for label, _, _ in cfgs} for sn in scens}
    with TemporaryDirectory(prefix="v131_") as tmp:
        work = Path(tmp)
        for sn, fact in scens.items():
            print(f"→ {sn}")
            for seed in seeds:
                scen = jitter_scenario(fact(), seed=seed)
                for label, d, ovs in cfgs:
                    db = work / f"{sn}_{seed}_{label.replace(' ', '_').replace('.', '_').replace('+', 'p')}.db"
                    r = run_doctrine(scen, d, db, fixtures_dir=fixtures,
                                     evaluate_rejections=True,
                                     late_threshold_days=3,
                                     param_overrides=ovs)
                    results[sn][label].append(measure(scen, r))

    lines = ["# QCDS V13.1 — BOM-op linkage ± V13.0 ± réaliste cap=480", ""]
    for sn, by_cfg in results.items():
        lines.append(f"## {sn}")
        lines.append("")
        lines.append("| Configuration | OTIF | C (€) | WIP pic | WIP σ |")
        lines.append("|---|---|---|---|---|")
        for label, _, _ in cfgs:
            ms = by_cfg[label]
            otif = statistics.mean(m["OTIF"] for m in ms)
            c = statistics.mean(m["C"] for m in ms)
            wp = statistics.mean(m["wip_peak"] for m in ms)
            wsd = statistics.mean(m["wip_sd"] for m in ms)
            cost = f"{c:,.0f} €".replace(",", " ")
            lines.append(
                f"| {label} | **{otif:.3f}** | {cost} | {wp:.1f} | {wsd:.2f} |"
            )
        lines.append("")

    DATA_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✓ {DATA_MD}")


if __name__ == "__main__":
    main()
