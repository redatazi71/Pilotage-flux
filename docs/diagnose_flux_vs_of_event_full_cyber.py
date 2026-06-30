"""FLUX+EVENT vs OF+EVENT sous V13 complet — est-ce que les flux contracts
ajoutent encore de la valeur quand les deux doctrines ont V13.1+V13.A ?

V13.0 ne s'applique qu'à EVENT (OF+EVENT n'a pas de scheduled_launch_day).
V13.1 et V13.A s'appliquent aux deux.
"""

from __future__ import annotations

import statistics
from pathlib import Path
from tempfile import TemporaryDirectory

from pilotage_flux.comparative.kpis import compute_kpis
from pilotage_flux.comparative.runner import run_doctrine
from pilotage_flux.comparative.scenario import (
    DOCTRINE_EVENT, DOCTRINE_OF_EVENT,
    baseline_xl_scenario,
    jitter_scenario,
    stress_cascade_nc_xl_scenario,
    stress_demand_spike_xl_scenario,
    stress_double_breakdown_xl_scenario,
)


def measure(scen, r) -> dict:
    k = compute_kpis(scen, r)
    wips = list(r.daily_wip.values())
    return {
        "OTIF": k.quantity_compliance * k.disponibility_so_level,
        "C": k.total_cost_eur,
        "wip_pic": max(wips) if wips else 0,
        "wip_sd": statistics.stdev(wips) if len(wips) > 1 else 0.0,
        "nerv": k.nervousness,
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
        ("OF+EVENT V11", DOCTRINE_OF_EVENT, None),
        ("OF+EVENT + V13.1+réal", DOCTRINE_OF_EVENT, {
            ("global", None, "bom_op_linkage_aware"): 1.0,
            ("global", None, "realistic_capacity_minutes_per_day"): 480.0,
        }),
        ("EVENT V11", DOCTRINE_EVENT, None),
        ("EVENT V13.0+V13.1+réal", DOCTRINE_EVENT, {
            ("global", None, "event_driven_smoothing_advance_days"): 5.0,
            ("global", None, "bom_op_linkage_aware"): 1.0,
            ("global", None, "realistic_capacity_minutes_per_day"): 480.0,
        }),
    ]
    seeds = list(range(3000, 3005))

    print(f"{'Scénario':28} {'Doctrine':32} {'OTIF':>6} {'Coût':>8} "
          f"{'WIP pic':>8} {'σ':>6}")
    print("-" * 95)
    for sn, fact in scens.items():
        for label, d, ovs in cfgs:
            ms = []
            with TemporaryDirectory(prefix="fve_") as tmp:
                work = Path(tmp)
                for seed in seeds:
                    scen = jitter_scenario(fact(), seed=seed)
                    safe = label.replace(' ', '_').replace('.', '_').replace('+', 'p').replace(',', '')
                    db = work / f"{sn}_{seed}_{safe}.db"
                    r = run_doctrine(scen, d, db, fixtures_dir=fixtures,
                                     evaluate_rejections=True,
                                     late_threshold_days=3,
                                     param_overrides=ovs)
                    ms.append(measure(scen, r))
            otif = statistics.mean(m["OTIF"] for m in ms)
            c = statistics.mean(m["C"] for m in ms)
            wp = statistics.mean(m["wip_pic"] for m in ms)
            wsd = statistics.mean(m["wip_sd"] for m in ms)
            print(f"{sn:28} {label:32} {otif:>6.3f} {c:>8.0f} "
                  f"{wp:>8.1f} {wsd:>6.2f}")
        print()


if __name__ == "__main__":
    main()
