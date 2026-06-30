"""Matrice QCDS 5 doctrines × 4 scénarios sous capacité réaliste (V13.A).

Réplique build_qcds_matrix_5_doctrines.py mais avec
`realistic_capacity_minutes_per_day = 480` (1 shift). Compare contre
le mode legacy pour quantifier l'effet du modèle de simulation sur
chaque objectif QCDS.
"""

from __future__ import annotations

import statistics
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


HERE = Path(__file__).resolve().parent
DATA_MD = HERE / "cadrage_v4_qcds_realistic_capacity.md"


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
    common_realistic = {
        ("global", None, "realistic_capacity_minutes_per_day"): 480.0,
    }
    doctrines = [
        ("OF", DOCTRINE_OF, common_realistic),
        ("OF+EVENT", DOCTRINE_OF_EVENT, common_realistic),
        ("FLUX", DOCTRINE_FLUX, common_realistic),
        ("EVENT", DOCTRINE_EVENT, common_realistic),
        ("EVENT V13.0", DOCTRINE_EVENT, {
            ("global", None, "event_driven_smoothing_advance_days"): 5.0,
            ("global", None, "realistic_capacity_minutes_per_day"): 480.0,
        }),
    ]
    seeds = list(range(3000, 3005))
    n = len(scens) * len(doctrines) * len(seeds)
    print(f"=== QCDS matrix réaliste (cap=480) : {n} runs ===\n")

    results = {sn: {dn: [] for dn, _, _ in doctrines} for sn in scens}
    with TemporaryDirectory(prefix="qcds_r_") as tmp:
        work = Path(tmp)
        for sn, fact in scens.items():
            print(f"→ {sn}")
            for seed in seeds:
                scen = jitter_scenario(fact(), seed=seed)
                for dn, d, ovs in doctrines:
                    db = work / f"{sn}_{seed}_{dn.replace(' ', '_').replace('.', '_')}.db"
                    r = run_doctrine(scen, d, db, fixtures_dir=fixtures,
                                     evaluate_rejections=True,
                                     late_threshold_days=3,
                                     param_overrides=ovs)
                    results[sn][dn].append(measure(scen, r))

    lines = ["# QCDS sous capacité réaliste (V13.A, cap=480 min/jour)",
             "",
             "Réplique de la matrice QCDS 5 doctrines mais avec la "
             "sérialisation `1 op / WS / jour` levée. La durée d'op est "
             "calculée comme `qty × unit_time / capa_factor` ; le WS "
             "peut traiter N ops tant que la somme < 480 min.", ""]
    for sn, by_doctrine in results.items():
        lines.append(f"## {sn}")
        lines.append("")
        lines.append("| Doctrine | Q | D | OTIF | C (€) | WIP pic | WIP σ |")
        lines.append("|---|---|---|---|---|---|---|")
        for dn, _, _ in doctrines:
            ms = by_doctrine[dn]
            q = statistics.mean(m["Q"] for m in ms)
            d_ = statistics.mean(m["D"] for m in ms)
            otif = statistics.mean(m["OTIF"] for m in ms)
            c = statistics.mean(m["C"] for m in ms)
            wp = statistics.mean(m["wip_peak"] for m in ms)
            wsd = statistics.mean(m["wip_sd"] for m in ms)
            cost = f"{c:,.0f} €".replace(",", " ")
            lines.append(
                f"| {dn} | {q:.3f} | {d_:.3f} | **{otif:.3f}** | "
                f"{cost} | {wp:.1f} | {wsd:.2f} |"
            )
        lines.append("")

    DATA_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✓ {DATA_MD}")


if __name__ == "__main__":
    main()
