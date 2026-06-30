"""V13.A — Test du modèle réaliste (N ops par WS par jour).

Mesure FLUX/EVENT V11/V13.0 + OF sous deux régimes :
  - legacy (realistic_capacity_minutes_per_day=0)
  - réaliste (realistic_capacity_minutes_per_day=480)

Question : avec la sérialisation levée, FLUX rattrape-t-il OF en OTIF
sans dégrader sa stabilité ?
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

    def run(d, label, ovs=None):
        with TemporaryDirectory(prefix="v13a_") as tmp:
            work = Path(tmp)
            r = run_doctrine(scen, d, work / "d.db", fixtures_dir=fixtures,
                             evaluate_rejections=False, param_overrides=ovs)
            k = compute_kpis(scen, r)
            wips = list(r.daily_wip.values())
            wip_sd = statistics.stdev(wips) if len(wips) > 1 else 0.0
            otif = k.quantity_compliance * k.disponibility_so_level
            print(f"  {label:30} OTIF={otif:.3f} C={k.total_cost_eur:>8.0f}€ "
                  f"WIP_pic={max(wips) if wips else 0:>4} σ={wip_sd:>5.2f}")

    print("=== baseline_xl, seed=42 ===\n")
    print("[Legacy : 1 op / WS / jour]")
    run(DOCTRINE_OF, "OF legacy")
    run(DOCTRINE_FLUX, "FLUX legacy")
    run(DOCTRINE_EVENT, "EVENT V11 legacy")
    run(DOCTRINE_EVENT, "EVENT V13.0 legacy", {
        ("global", None, "event_driven_smoothing_advance_days"): 5.0,
    })

    print("\n[Réaliste : 480 min / WS / jour]")
    for cap in (480, 960):
        print(f"\n  cap = {cap} min/jour :")
        run(DOCTRINE_OF, f"OF cap={cap}", {
            ("global", None, "realistic_capacity_minutes_per_day"): float(cap),
        })
        run(DOCTRINE_FLUX, f"FLUX cap={cap}", {
            ("global", None, "realistic_capacity_minutes_per_day"): float(cap),
        })
        run(DOCTRINE_EVENT, f"EVENT V11 cap={cap}", {
            ("global", None, "realistic_capacity_minutes_per_day"): float(cap),
        })
        run(DOCTRINE_EVENT, f"EVENT V13.0 cap={cap}", {
            ("global", None, "event_driven_smoothing_advance_days"): 5.0,
            ("global", None, "realistic_capacity_minutes_per_day"): float(cap),
        })


if __name__ == "__main__":
    main()
