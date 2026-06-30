"""V13.1 — Test BOM-op linkage : OFs phasés ne bloquent plus sur composants non-immédiats.

Compare baseline_xl :
  - FLUX legacy        : V11, BOM cascade strict
  - FLUX V13.1         : bom_op_linkage_aware = 1
  - EVENT V13.0 + V13.1 : combinaison
  - + variantes en mode réaliste pour mesurer additivité
"""

from __future__ import annotations

import statistics
from pathlib import Path
from tempfile import TemporaryDirectory

from pilotage_flux.comparative.kpis import compute_kpis
from pilotage_flux.comparative.runner import run_doctrine
from pilotage_flux.comparative.scenario import (
    DOCTRINE_EVENT, DOCTRINE_FLUX, DOCTRINE_OF,
    baseline_xl_scenario, jitter_scenario,
)


def main() -> None:
    fixtures = Path("data/fixtures_extended")
    scen = jitter_scenario(baseline_xl_scenario(), seed=42)

    def go(label, d, ovs=None):
        with TemporaryDirectory(prefix="v131_") as tmp:
            work = Path(tmp)
            r = run_doctrine(scen, d, work / "d.db", fixtures_dir=fixtures,
                             evaluate_rejections=False, param_overrides=ovs)
            k = compute_kpis(scen, r)
            wips = list(r.daily_wip.values())
            wip_sd = statistics.stdev(wips) if len(wips) > 1 else 0.0
            otif = k.quantity_compliance * k.disponibility_so_level
            stuck = sum(1 for v in r.of_qty_good.values() if False) or 0
            print(f"  {label:35} OTIF={otif:.3f} C={k.total_cost_eur:>7.0f}€ "
                  f"WIP_pic={max(wips) if wips else 0:>3} σ={wip_sd:>5.2f}")

    print("=== baseline_xl, seed=42 ===\n")
    print("[Legacy WS-sérialisation, sans V13.1]")
    go("OF", DOCTRINE_OF)
    go("FLUX legacy", DOCTRINE_FLUX)
    go("EVENT V11", DOCTRINE_EVENT)
    go("EVENT V13.0", DOCTRINE_EVENT, {
        ("global", None, "event_driven_smoothing_advance_days"): 5.0,
    })

    print("\n[Legacy WS-sérialisation, AVEC V13.1]")
    go("FLUX V13.1", DOCTRINE_FLUX, {
        ("global", None, "bom_op_linkage_aware"): 1.0,
    })
    go("EVENT V11 + V13.1", DOCTRINE_EVENT, {
        ("global", None, "bom_op_linkage_aware"): 1.0,
    })
    go("EVENT V13.0 + V13.1", DOCTRINE_EVENT, {
        ("global", None, "event_driven_smoothing_advance_days"): 5.0,
        ("global", None, "bom_op_linkage_aware"): 1.0,
    })

    print("\n[Mode réaliste cap=480, AVEC V13.1]")
    go("FLUX V13.1 + réaliste", DOCTRINE_FLUX, {
        ("global", None, "bom_op_linkage_aware"): 1.0,
        ("global", None, "realistic_capacity_minutes_per_day"): 480.0,
    })
    go("EVENT V13.0 + V13.1 + réaliste", DOCTRINE_EVENT, {
        ("global", None, "event_driven_smoothing_advance_days"): 5.0,
        ("global", None, "bom_op_linkage_aware"): 1.0,
        ("global", None, "realistic_capacity_minutes_per_day"): 480.0,
    })


if __name__ == "__main__":
    main()
