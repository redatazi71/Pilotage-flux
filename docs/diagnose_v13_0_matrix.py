"""V13.0 — Matrice doctrinale 2×2 (flux × event sourcing) avec V13.0.

Reprend l'analyse L8.4 stratifiée :

  - OF        : (flux ✗, event ✗) → V0 baseline
  - OF+EVENT  : (flux ✗, event ✓) → événements sans contrats
  - FLUX      : (flux ✓, event ✗) → contrats sans événements
  - EVENT     : (flux ✓, event ✓) → V3 complet
  - EVENT V13.0 : EVENT + event-driven smoothing reactivity

Mesure :
  - Δ event sourcing sur OF (OF+EVENT - OF)
  - Δ event sourcing sur FLUX (EVENT - FLUX) (avant V13.0 = 0)
  - Δ event sourcing sur FLUX avec V13.0 (EVENT V13.0 - FLUX)
  - Δ flux contracts sur EVENT (EVENT V13.0 - OF+EVENT)
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from pilotage_flux.comparative.kpis import compute_kpis
from pilotage_flux.comparative.runner import run_doctrine
from pilotage_flux.comparative.scenario import (
    DOCTRINE_EVENT, DOCTRINE_FLUX, DOCTRINE_OF, DOCTRINE_OF_EVENT,
    baseline_xl_scenario,
    jitter_scenario,
    stress_cascade_nc_xl_scenario,
    stress_demand_spike_xl_scenario,
    stress_double_breakdown_xl_scenario,
)


def kpi(scen, result):
    k = compute_kpis(scen, result)
    return {
        "otif": k.quantity_compliance * k.disponibility_so_level,
        "c": k.total_cost_eur,
    }


def main() -> None:
    fixtures = Path("data/fixtures_extended")
    scens = {
        "baseline_xl": baseline_xl_scenario,
        "stress_double_breakdown_xl": stress_double_breakdown_xl_scenario,
        "stress_cascade_nc_xl": stress_cascade_nc_xl_scenario,
        "stress_demand_spike_xl": stress_demand_spike_xl_scenario,
    }

    print(f"\n{'Scénario':28} {'OF':>7} {'OF+E':>7} {'FLUX':>7} "
          f"{'EVENT':>7} {'EVENT V13.0':>13}")
    print("-" * 80)

    rows = {}
    for sn, fact in scens.items():
        with TemporaryDirectory(prefix="m13_") as tmp:
            work = Path(tmp)
            scen = jitter_scenario(fact(), seed=42)

            r_of = run_doctrine(scen, DOCTRINE_OF, work / "of.db",
                                fixtures_dir=fixtures,
                                evaluate_rejections=True,
                                late_threshold_days=3)
            r_ofe = run_doctrine(scen, DOCTRINE_OF_EVENT, work / "ofe.db",
                                 fixtures_dir=fixtures,
                                 evaluate_rejections=True,
                                 late_threshold_days=3)
            r_f = run_doctrine(scen, DOCTRINE_FLUX, work / "f.db",
                               fixtures_dir=fixtures,
                               evaluate_rejections=True,
                               late_threshold_days=3)
            r_e = run_doctrine(scen, DOCTRINE_EVENT, work / "e.db",
                               fixtures_dir=fixtures,
                               evaluate_rejections=True,
                               late_threshold_days=3)
            r_e13 = run_doctrine(scen, DOCTRINE_EVENT, work / "e13.db",
                                 fixtures_dir=fixtures,
                                 evaluate_rejections=True,
                                 late_threshold_days=3,
                                 param_overrides={
                                     ("global", None,
                                      "event_driven_smoothing_advance_days"): 5.0,
                                 })

            of = kpi(scen, r_of)
            ofe = kpi(scen, r_ofe)
            f = kpi(scen, r_f)
            e = kpi(scen, r_e)
            e13 = kpi(scen, r_e13)
            rows[sn] = (of, ofe, f, e, e13)

        print(f"{sn:28} {of['otif']:>7.3f} {ofe['otif']:>7.3f} "
              f"{f['otif']:>7.3f} {e['otif']:>7.3f} "
              f"{e13['otif']:>13.3f}")

    # Deltas doctrinaux
    print(f"\n{'Δ doctrinal':28} {'event/OF':>10} {'event/FLUX':>12} "
          f"{'event V13.0/FLUX':>17} {'flux/event':>11}")
    print("-" * 85)
    for sn, (of, ofe, f, e, e13) in rows.items():
        d_eonOF = (ofe["otif"] - of["otif"]) * 100
        d_eonFLUX_v11 = (e["otif"] - f["otif"]) * 100
        d_eonFLUX_v13 = (e13["otif"] - f["otif"]) * 100
        d_flux_on_event = (e13["otif"] - ofe["otif"]) * 100
        print(f"{sn:28} {d_eonOF:>+9.1f}pp {d_eonFLUX_v11:>+11.1f}pp "
              f"{d_eonFLUX_v13:>+16.1f}pp {d_flux_on_event:>+10.1f}pp")

    print("\nLectures :")
    print("  event/OF          : apport du event sourcing sur OF baseline")
    print("  event/FLUX V11    : apport du event sourcing sur FLUX (était 0)")
    print("  event V13.0/FLUX  : apport event + V13.0 sur FLUX (réel)")
    print("  flux/event V13.0  : apport des contrats flux dans une doctrine event")


if __name__ == "__main__":
    main()
