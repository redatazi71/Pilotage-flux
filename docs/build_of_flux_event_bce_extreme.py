"""Étude comparative stress EXTRÊME : OF / OF+EVENT / FLUX+EVENT ± BCE.

Vs `build_of_flux_event_bce_study.py` (60j / 8 hazards / 4 types):
  - Horizon 120j (2×)
  - 20 hazards (2.5×)
  - 25 SOs (1.7×)
  - 5 types de hazards actifs (dont logistic_delay)
  - Breakdown plus long/plus sévère
  - Scrap plus élevé
  - PO delay plus long

Objectif : franchir les zones de tolérance BCE et faire ressortir
FLUX+EVENT vs OF+EVENT sur QCDS+OTIF+WIP+rupture+recovery.

Sortie :
  - docs/of_flux_event_bce_extreme_runs.csv
  - docs/of_flux_event_bce_extreme_summary.json
  - docs/of_flux_event_bce_extreme_report.md
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pilotage_flux.comparative.kpis import compute_kpis
from pilotage_flux.comparative.random_scenario import (
    RandomScenarioSpec, generate_random_scenario,
)
from pilotage_flux.comparative.resilience import compute_time_to_recover
from pilotage_flux.comparative.runner import run_doctrine
from pilotage_flux.comparative.scenario import (
    DOCTRINE_EVENT,
    DOCTRINE_EVENT_BCE,
    DOCTRINE_OF,
    DOCTRINE_OF_EVENT,
    DOCTRINE_OF_EVENT_BCE,
    HAZARD_BREAKDOWN,
    HAZARD_LOGISTIC_DELAY,
    HAZARD_PO_DELAY,
    HAZARD_QUALITY_NC,
    HAZARD_URGENT_ORDER,
)
from pilotage_flux.data_factory import DEFAULT_SPEC, generate_random_fixtures


HORIZON_DAYS = 120
N_HAZARDS = 20
N_SALES_ORDERS = 25
N_SEEDS_DEFAULT = 5

# 5 configurations testées
CONFIGS = [
    ("OF",              DOCTRINE_OF),
    ("OF+EVENT",        DOCTRINE_OF_EVENT),
    ("FLUX+EVENT",      DOCTRINE_EVENT),
    ("OF+EVENT+BCE",    DOCTRINE_OF_EVENT_BCE),
    ("FLUX+EVENT+BCE",  DOCTRINE_EVENT_BCE),
]

HERE = Path(__file__).resolve().parent
RUNS_CSV = HERE / "of_flux_event_bce_extreme_runs.csv"
SUMMARY_JSON = HERE / "of_flux_event_bce_extreme_summary.json"
REPORT_MD = HERE / "of_flux_event_bce_extreme_report.md"


def _extreme_spec() -> RandomScenarioSpec:
    """5 hazards actifs, sévérité pousée."""
    return RandomScenarioSpec(
        n_hazards=N_HAZARDS,
        n_sales_orders=N_SALES_ORDERS,
        horizon_days=HORIZON_DAYS,
        hazard_kinds=[
            HAZARD_BREAKDOWN, HAZARD_QUALITY_NC,
            HAZARD_PO_DELAY, HAZARD_URGENT_ORDER,
            HAZARD_LOGISTIC_DELAY,
        ],
        hazard_weights={
            HAZARD_BREAKDOWN: 0.30,
            HAZARD_QUALITY_NC: 0.25,
            HAZARD_PO_DELAY: 0.20,
            HAZARD_URGENT_ORDER: 0.15,
            HAZARD_LOGISTIC_DELAY: 0.10,
        },
        breakdown_duration_range=(4, 8),
        breakdown_factor_range=(2.0, 4.0),
        nc_scrap_range=(20, 45),
        po_delay_range=(5, 15),
        urgent_qty_range=(30, 80),
        logistic_block_range=(3, 6),
    )


def _run_one(scen, doctrine, work, fix_dir, config_tag) -> dict:
    db = work / f"{config_tag}_{scen.seed}.db"
    try:
        result = run_doctrine(scen, doctrine, db, fixtures_dir=fix_dir)
        k = compute_kpis(scen, result)
        wip_vals = list(result.daily_wip.values())
        wip_sd = statistics.stdev(wip_vals) if len(wip_vals) >= 2 else 0.0
        rupture_pct = (
            k.so_rejected / k.so_total if k.so_total > 0 else 0.0
        )
        first_hazard_day = min((h.day for h in scen.hazards), default=3)
        recovery_days = compute_time_to_recover(result, shock_day=first_hazard_day)
        return {
            "seed": scen.seed,
            "config_tag": config_tag,
            "doctrine": doctrine,
            "status": "ok",
            "otif": k.quantity_compliance * k.disponibility_so_level,
            "q_compliance": k.quantity_compliance,
            "d_dispo": k.disponibility_so_level,
            "cost_total": k.total_cost_eur,
            "cost_per_u": k.cost_per_unit_delivered,
            "wip_avg": k.wip_avg,
            "wip_sd": wip_sd,
            "nervousness": k.nervousness,
            "so_total": k.so_total,
            "so_rejected": k.so_rejected,
            "rupture_pct": rupture_pct,
            "recovery_days": recovery_days,
            "of_total": k.of_total,
            "of_closed": k.of_closed,
            "of_closed_ratio": (
                k.of_closed / k.of_total if k.of_total > 0 else 0.0
            ),
            "qty_delivered": k.qty_delivered_total,
        }
    except Exception as e:
        return {
            "seed": scen.seed,
            "config_tag": config_tag,
            "doctrine": doctrine,
            "status": "crashed",
            "error": str(e)[:120],
        }


def _agg_by_config(runs: list[dict]) -> dict:
    from collections import defaultdict
    by_tag: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        if r.get("status") == "ok":
            by_tag[r["config_tag"]].append(r)

    def mean(rs, key):
        vals = [r.get(key, 0.0) for r in rs]
        return statistics.mean(vals) if vals else 0.0

    out = {}
    for tag, rs in by_tag.items():
        if not rs:
            continue
        out[tag] = {
            "n_runs": len(rs),
            "otif_mean": mean(rs, "otif"),
            "q_mean": mean(rs, "q_compliance"),
            "d_mean": mean(rs, "d_dispo"),
            "cost_per_u_mean": mean(rs, "cost_per_u"),
            "wip_avg_mean": mean(rs, "wip_avg"),
            "wip_sd_mean": mean(rs, "wip_sd"),
            "nervousness_mean": mean(rs, "nervousness"),
            "rupture_pct_mean": mean(rs, "rupture_pct"),
            "recovery_days_mean": mean(rs, "recovery_days"),
            "of_closed_ratio_mean": mean(rs, "of_closed_ratio"),
        }
    return out


def _write_report(agg: dict) -> None:
    def get(tag, k, fmt="{:.3f}"):
        v = agg.get(tag, {}).get(k)
        return fmt.format(v) if v is not None else "—"

    lines = [
        "# Étude comparative STRESS EXTRÊME OF / OF+EVENT / FLUX+EVENT ± BCE",
        "",
        f"Horizon {HORIZON_DAYS}j, {N_HAZARDS} hazards (5 types), "
        f"{N_SALES_ORDERS} SOs, {N_SEEDS_DEFAULT} seeds par config.",
        "",
        "Sévérité amplifiée : breakdown 4-8j × 2-4×, scrap 20-45, "
        "PO delay 5-15j, blocage logistique 3-6j.",
        "",
        "## Tableau QCDS + OTIF + WIP + Rupture + Recovery",
        "",
        "| Configuration | OTIF | Q | D | €/u | WIP moy | WIP σ | "
        "Nervosité | Rupture % | Recovery j |",
        "|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|",
    ]
    for tag, _ in CONFIGS:
        lines.append(
            f"| {tag} | {get(tag, 'otif_mean')} | "
            f"{get(tag, 'q_mean')} | {get(tag, 'd_mean')} | "
            f"{get(tag, 'cost_per_u_mean', '{:.2f}')} | "
            f"{get(tag, 'wip_avg_mean', '{:.2f}')} | "
            f"{get(tag, 'wip_sd_mean', '{:.2f}')} | "
            f"{get(tag, 'nervousness_mean', '{:.3f}')} | "
            f"{get(tag, 'rupture_pct_mean', '{:.1%}')} | "
            f"{get(tag, 'recovery_days_mean', '{:.1f}')} |"
        )
    lines.append("")
    lines.append("## Différentiels doctrinaux")
    lines.append("")
    lines.append("### FLUX+EVENT vs OF vs OF+EVENT")
    lines.append("")
    otif_of = agg.get("OF", {}).get("otif_mean", 0)
    otif_ofe = agg.get("OF+EVENT", {}).get("otif_mean", 0)
    otif_fe = agg.get("FLUX+EVENT", {}).get("otif_mean", 0)
    lines.append(f"- OTIF : OF {otif_of:.3f}, OF+EVENT {otif_ofe:.3f}, "
                  f"FLUX+EVENT {otif_fe:.3f}")
    cost_of = agg.get("OF", {}).get("cost_per_u_mean", 0)
    cost_ofe = agg.get("OF+EVENT", {}).get("cost_per_u_mean", 0)
    cost_fe = agg.get("FLUX+EVENT", {}).get("cost_per_u_mean", 0)
    lines.append(f"- €/unité : OF {cost_of:.2f}, OF+EVENT {cost_ofe:.2f}, "
                  f"FLUX+EVENT {cost_fe:.2f}")
    wip_of = agg.get("OF", {}).get("wip_sd_mean", 0)
    wip_ofe = agg.get("OF+EVENT", {}).get("wip_sd_mean", 0)
    wip_fe = agg.get("FLUX+EVENT", {}).get("wip_sd_mean", 0)
    lines.append(f"- WIP σ : OF {wip_of:.2f}, OF+EVENT {wip_ofe:.2f}, "
                  f"FLUX+EVENT {wip_fe:.2f}")
    rup_of = agg.get("OF", {}).get("rupture_pct_mean", 0)
    rup_ofe = agg.get("OF+EVENT", {}).get("rupture_pct_mean", 0)
    rup_fe = agg.get("FLUX+EVENT", {}).get("rupture_pct_mean", 0)
    lines.append(f"- Rupture : OF {rup_of:.1%}, OF+EVENT {rup_ofe:.1%}, "
                  f"FLUX+EVENT {rup_fe:.1%}")
    rec_of = agg.get("OF", {}).get("recovery_days_mean", 0)
    rec_ofe = agg.get("OF+EVENT", {}).get("recovery_days_mean", 0)
    rec_fe = agg.get("FLUX+EVENT", {}).get("recovery_days_mean", 0)
    lines.append(f"- Recovery : OF {rec_of:.1f}j, OF+EVENT {rec_ofe:.1f}j, "
                  f"FLUX+EVENT {rec_fe:.1f}j")
    lines.append("")
    lines.append("### BCE apport (avec vs sans)")
    lines.append("")
    for base, bce in [("OF+EVENT", "OF+EVENT+BCE"),
                        ("FLUX+EVENT", "FLUX+EVENT+BCE")]:
        b = agg.get(base, {})
        c = agg.get(bce, {})
        d_otif = c.get("otif_mean", 0) - b.get("otif_mean", 0)
        d_cost = c.get("cost_per_u_mean", 0) - b.get("cost_per_u_mean", 0)
        d_wip = c.get("wip_sd_mean", 0) - b.get("wip_sd_mean", 0)
        d_rec = c.get("recovery_days_mean", 0) - b.get("recovery_days_mean", 0)
        d_rup = c.get("rupture_pct_mean", 0) - b.get("rupture_pct_mean", 0)
        lines.append(
            f"- **{bce} vs {base}** : "
            f"ΔOTIF {d_otif:+.3f}, "
            f"Δ€/u {d_cost:+.2f}, "
            f"ΔWIP σ {d_wip:+.2f}, "
            f"Δrecovery {d_rec:+.1f}j, "
            f"Δrupture {d_rup:+.1%}"
        )
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓ {REPORT_MD}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=N_SEEDS_DEFAULT)
    args = parser.parse_args()
    seeds = list(range(800, 800 + args.seeds))
    n_total = len(seeds) * len(CONFIGS)
    print(f"=== Étude STRESS EXTRÊME OF / OF+EVENT / FLUX+EVENT ± BCE ===")
    print(f"Horizon {HORIZON_DAYS}j × {N_HAZARDS} hazards × "
          f"{len(CONFIGS)} configs × {len(seeds)} seeds = "
          f"{n_total} runs")

    all_runs: list[dict] = []
    with TemporaryDirectory(prefix="of_flux_bce_x_") as tmp:
        work = Path(tmp)
        fix_dir = work / "fix"
        generate_random_fixtures(DEFAULT_SPEC, seed=42, out_dir=fix_dir)
        spec = _extreme_spec()
        done = crashed = 0
        for seed in seeds:
            scen = generate_random_scenario(spec, seed=seed,
                                             fixtures_dir=fix_dir)
            for tag, doctrine in CONFIGS:
                r = _run_one(scen, doctrine, work, fix_dir, tag)
                all_runs.append(r)
                done += 1
                if r.get("status") == "crashed":
                    crashed += 1
                if done % 5 == 0 or done == n_total:
                    print(f"  ... {done}/{n_total} "
                          f"({crashed} crashs)")

    fields = sorted({k for r in all_runs for k in r.keys()})
    with RUNS_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in all_runs:
            w.writerow(r)
    print(f"\n✓ {RUNS_CSV}")

    agg = _agg_by_config(all_runs)
    SUMMARY_JSON.write_text(json.dumps(agg, indent=2, default=str))
    print(f"✓ {SUMMARY_JSON}")

    _write_report(agg)

    print("\n=== Résumé stress extrême ===")
    print(f"{'Config':<20} {'OTIF':>7} {'€/u':>7} "
          f"{'WIP σ':>7} {'Rupt%':>7} {'Rec j':>6}")
    for tag, _ in CONFIGS:
        a = agg.get(tag, {})
        if not a:
            continue
        print(f"{tag:<20} {a['otif_mean']:>7.3f} "
              f"{a['cost_per_u_mean']:>7.2f} "
              f"{a['wip_sd_mean']:>7.2f} "
              f"{a['rupture_pct_mean']:>7.1%} "
              f"{a['recovery_days_mean']:>6.1f}")
    return 0 if crashed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
