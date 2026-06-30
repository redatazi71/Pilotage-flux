"""V13.0 — Event-driven smoothing reactivity : EVENT diverge-t-il enfin de FLUX ?

Compare FLUX (V11) vs EVENT (V11) vs EVENT V13.0 (advance_days=5) sur les
4 scénarios stress XL.

L'objectif n'est PAS d'atteindre 95 % d'OTIF (V12.7 le fait via brute
force). L'objectif EST de montrer que la doctrine EVENT a maintenant un
chemin DIFFÉRENCIÉ de FLUX qui se traduit par un Δ OTIF mesurable
(positif ou négatif, peu importe — l'important est de casser l'égalité
parfaite).
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from pilotage_flux.comparative.kpis import compute_kpis
from pilotage_flux.comparative.runner import run_doctrine
from pilotage_flux.comparative.scenario import (
    DOCTRINE_EVENT, DOCTRINE_FLUX,
    baseline_xl_scenario,
    jitter_scenario,
    stress_cascade_nc_xl_scenario,
    stress_demand_spike_xl_scenario,
    stress_double_breakdown_xl_scenario,
)
from pilotage_flux.db import db_session


def kpi(scen, db_path, result):
    k = compute_kpis(scen, result)
    return {
        "q": k.quantity_compliance,
        "d": k.disponibility_so_level,
        "otif": k.quantity_compliance * k.disponibility_so_level,
        "c": k.total_cost_eur,
        "correctives": len(result.corrective_actions_applied),
    }


def main() -> None:
    fixtures = Path("data/fixtures_extended")
    scens = {
        "baseline_xl": baseline_xl_scenario,
        "stress_double_breakdown_xl": stress_double_breakdown_xl_scenario,
        "stress_cascade_nc_xl": stress_cascade_nc_xl_scenario,
        "stress_demand_spike_xl": stress_demand_spike_xl_scenario,
    }

    print(f"{'Scénario':30} {'FLUX V11':>10} {'EVENT V11':>10} "
          f"{'EVENT V13.0':>12} {'Δ E13-E11':>10}")
    print("-" * 80)

    for sn, fact in scens.items():
        with TemporaryDirectory(prefix="v130_") as tmp:
            work = Path(tmp)
            scen = jitter_scenario(fact(), seed=42)

            rf = run_doctrine(scen, DOCTRINE_FLUX, work / "f.db",
                              fixtures_dir=fixtures,
                              evaluate_rejections=True,
                              late_threshold_days=3)
            re_ = run_doctrine(scen, DOCTRINE_EVENT, work / "e.db",
                               fixtures_dir=fixtures,
                               evaluate_rejections=True,
                               late_threshold_days=3)
            re13 = run_doctrine(scen, DOCTRINE_EVENT, work / "e13.db",
                                fixtures_dir=fixtures,
                                evaluate_rejections=True,
                                late_threshold_days=3,
                                param_overrides={
                                    ("global", None,
                                     "event_driven_smoothing_advance_days"): 5.0,
                                })

            kf = kpi(scen, work / "f.db", rf)
            ke = kpi(scen, work / "e.db", re_)
            ke13 = kpi(scen, work / "e13.db", re13)

        print(f"{sn:30} {kf['otif']:>10.3f} {ke['otif']:>10.3f} "
              f"{ke13['otif']:>12.3f} {(ke13['otif'] - ke['otif']) * 100:>+9.1f}pp")


if __name__ == "__main__":
    main()
