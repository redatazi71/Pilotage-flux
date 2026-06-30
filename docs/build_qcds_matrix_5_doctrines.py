"""Matrice QCDS sur 5 doctrines × 4 scénarios stress XL.

Mesure les 4 objectifs QCDS (Quality, Cost, Delivery, Stability) pour
identifier où chaque doctrine excelle. Réponse honnête à la question
« si FLUX n'est pas OTIF-first, est-ce que c'est S-first ou C-first ? ».

Doctrines :
  - OF             : V0 baseline
  - OF+EVENT       : OF + event sourcing
  - FLUX           : V1+V2 (flux contracts + freeze)
  - EVENT          : V3 = FLUX + event sourcing (V11)
  - EVENT V13.0    : EVENT + event-driven smoothing reactivity

Métriques :
  - Q : quantity_compliance × disponibility_so_level (= OTIF)
  - C : total_cost_eur
  - D : disponibility_so_level (déjà dans Q)
  - S : composite stabilité = inv(1 + nervousness + wip_peak_normalized)

Pour fixer les idées on rapporte séparément :
  - OTIF (Q × D, lecture standard)
  - Coût total
  - Nervosité (aps_recalculations / horizon_days)
  - WIP pic (max sur l'horizon)
  - WIP variance (sd des WIP journaliers)
"""

from __future__ import annotations

import math
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
DATA_MD = HERE / "cadrage_v4_qcds_matrix_5_doctrines.md"


def measure(scen, result) -> dict:
    k = compute_kpis(scen, result)
    wips = list(result.daily_wip.values())
    wip_peak = max(wips) if wips else 0
    wip_sd = statistics.stdev(wips) if len(wips) > 1 else 0.0
    nervousness = k.nervousness
    return {
        "Q": k.quantity_compliance,
        "D": k.disponibility_so_level,
        "OTIF": k.quantity_compliance * k.disponibility_so_level,
        "C": k.total_cost_eur,
        "nervousness": nervousness,
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
    doctrines = [
        ("OF", DOCTRINE_OF, None),
        ("OF+EVENT", DOCTRINE_OF_EVENT, None),
        ("FLUX", DOCTRINE_FLUX, None),
        ("EVENT", DOCTRINE_EVENT, None),
        ("EVENT V13.0", DOCTRINE_EVENT, {
            ("global", None, "event_driven_smoothing_advance_days"): 5.0,
        }),
    ]
    seeds = list(range(3000, 3005))

    print(f"=== QCDS matrix : {len(scens)} scénarios × {len(doctrines)} "
          f"doctrines × {len(seeds)} seeds = "
          f"{len(scens) * len(doctrines) * len(seeds)} runs ===\n")

    results = {sn: {dn: [] for dn, _, _ in doctrines} for sn in scens}
    with TemporaryDirectory(prefix="qcds_") as tmp:
        work = Path(tmp)
        for sn, fact in scens.items():
            print(f"→ {sn}")
            for seed in seeds:
                scen = jitter_scenario(fact(), seed=seed)
                for dn, d, ovs in doctrines:
                    db = work / f"{sn}_{seed}_{dn}.db".replace(" ", "_").replace(".", "_")
                    r = run_doctrine(scen, d, db, fixtures_dir=fixtures,
                                     evaluate_rejections=True,
                                     late_threshold_days=3,
                                     param_overrides=ovs)
                    results[sn][dn].append(measure(scen, r))

    # Aggregate
    lines = ["# Matrice QCDS 5 doctrines × 4 scénarios (Option 1)",
             "",
             "Mesure des 4 objectifs QCDS sur la matrice doctrinale étendue.",
             "5 seeds par cellule (3000-3004). Métriques moyennées.",
             ""]
    for sn, by_doctrine in results.items():
        lines.append(f"## {sn}")
        lines.append("")
        lines.append("| Doctrine | Q (quantity) | D (dispo SO) | OTIF | "
                     "C (€) | Nervosité | WIP pic | WIP σ |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for dn, _, _ in doctrines:
            ms = by_doctrine[dn]
            q = statistics.mean(m["Q"] for m in ms)
            d_ = statistics.mean(m["D"] for m in ms)
            otif = statistics.mean(m["OTIF"] for m in ms)
            c = statistics.mean(m["C"] for m in ms)
            nerv = statistics.mean(m["nervousness"] for m in ms)
            wp = statistics.mean(m["wip_peak"] for m in ms)
            wsd = statistics.mean(m["wip_sd"] for m in ms)
            cost = f"{c:,.0f} €".replace(",", " ")
            lines.append(
                f"| {dn} | {q:.3f} | {d_:.3f} | **{otif:.3f}** | "
                f"{cost} | {nerv:.2f} | {wp:.1f} | {wsd:.2f} |"
            )
        lines.append("")

    # Ranking par scénario
    lines.append("## Classements doctrinaux par objectif QCDS")
    lines.append("")
    lines.append("Pour chaque scénario, doctrine gagnante par objectif. "
                 "Q et D : plus haut = mieux. C et S (= 1/(1+wip_sd)) : "
                 "plus bas wip_sd = plus stable = mieux.")
    lines.append("")
    lines.append("| Scénario | Q max | C min (coût) | OTIF max | "
                 "Stabilité max (WIP σ min) |")
    lines.append("|---|---|---|---|---|")
    for sn, by_doctrine in results.items():
        means = {
            dn: {
                "Q": statistics.mean(m["Q"] for m in ms),
                "OTIF": statistics.mean(m["OTIF"] for m in ms),
                "C": statistics.mean(m["C"] for m in ms),
                "wip_sd": statistics.mean(m["wip_sd"] for m in ms),
            }
            for dn, ms in by_doctrine.items()
        }
        win_q = max(means, key=lambda d: means[d]["Q"])
        win_c = min(means, key=lambda d: means[d]["C"])
        win_otif = max(means, key=lambda d: means[d]["OTIF"])
        win_s = min(means, key=lambda d: means[d]["wip_sd"])
        lines.append(
            f"| {sn} | **{win_q}** ({means[win_q]['Q']:.3f}) | "
            f"**{win_c}** ({means[win_c]['C']:,.0f} €) | "
            f"**{win_otif}** ({means[win_otif]['OTIF']:.3f}) | "
            f"**{win_s}** (σ {means[win_s]['wip_sd']:.2f}) |"
        )
        lines[-1] = lines[-1].replace(",", " ")
    DATA_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✓ {DATA_MD}")


if __name__ == "__main__":
    main()
