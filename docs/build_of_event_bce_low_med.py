"""Étude OF / OF+EVENT / OF+EVENT+BCE en stress FAIBLE et MOYEN.

Complète l'étude cadrée précédemment (stress fort 60j×8 et stress
extrême 120j×20) par les 2 niveaux inférieurs :

  - stress faible : 30j × 3 hazards
  - stress moyen  : 45j × 5 hazards

Focus 3 configurations pour isoler la question BCE :
  - OF (baseline)
  - OF+EVENT
  - OF+EVENT+BCE

Sortie :
  - docs/of_event_bce_low_med_runs.csv
  - docs/of_event_bce_low_med_summary.json
  - docs/of_event_bce_low_med_report.md
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
    DOCTRINE_OF,
    DOCTRINE_OF_EVENT,
    DOCTRINE_OF_EVENT_BCE,
)
from pilotage_flux.data_factory import DEFAULT_SPEC, generate_random_fixtures


CONFIGS = [
    ("OF",           DOCTRINE_OF),
    ("OF+EVENT",     DOCTRINE_OF_EVENT),
    ("OF+EVENT+BCE", DOCTRINE_OF_EVENT_BCE),
]

STRESS_LEVELS = [
    ("faible", {"horizon_days": 30, "n_hazards": 3, "n_sales_orders": 8}),
    ("moyen",  {"horizon_days": 45, "n_hazards": 5, "n_sales_orders": 12}),
]

N_SEEDS_DEFAULT = 5

HERE = Path(__file__).resolve().parent
RUNS_CSV = HERE / "of_event_bce_low_med_runs.csv"
SUMMARY_JSON = HERE / "of_event_bce_low_med_summary.json"
REPORT_MD = HERE / "of_event_bce_low_med_report.md"


def _run_one(scen, doctrine, work, fix_dir, tag, level) -> dict:
    db = work / f"{level}_{tag}_{scen.seed}.db"
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
            "level": level,
            "seed": scen.seed,
            "config_tag": tag,
            "doctrine": doctrine,
            "status": "ok",
            "otif": k.quantity_compliance * k.disponibility_so_level,
            "q_compliance": k.quantity_compliance,
            "d_dispo": k.disponibility_so_level,
            "cost_per_u": k.cost_per_unit_delivered,
            "wip_avg": k.wip_avg,
            "wip_sd": wip_sd,
            "nervousness": k.nervousness,
            "so_total": k.so_total,
            "so_rejected": k.so_rejected,
            "rupture_pct": rupture_pct,
            "recovery_days": recovery_days,
            "of_total": k.of_total,
        }
    except Exception as e:
        return {
            "level": level,
            "seed": scen.seed,
            "config_tag": tag,
            "doctrine": doctrine,
            "status": "crashed",
            "error": str(e)[:120],
        }


def _agg(runs: list[dict]) -> dict:
    from collections import defaultdict
    by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in runs:
        if r.get("status") == "ok":
            by_key[(r["level"], r["config_tag"])].append(r)

    def mean(rs, key):
        vals = [r.get(key, 0.0) for r in rs]
        return statistics.mean(vals) if vals else 0.0

    out: dict[str, dict[str, dict]] = {}
    for (level, tag), rs in by_key.items():
        out.setdefault(level, {})[tag] = {
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
        }
    return out


def _write_report(agg: dict) -> None:
    lines = [
        "# Étude OF / OF+EVENT / OF+EVENT+BCE — Stress faible et moyen",
        "",
        "Complément aux études stress fort (60j × 8) et extrême (120j × 20). "
        "5 seeds par niveau et par configuration.",
        "",
    ]
    for level in ("faible", "moyen"):
        a = agg.get(level, {})
        if not a:
            continue
        spec = dict(STRESS_LEVELS)[level]
        lines.append(f"## Stress {level.upper()} "
                     f"({spec['horizon_days']}j × {spec['n_hazards']} hazards)")
        lines.append("")
        lines.append(
            "| Configuration | OTIF | Q | D | €/u | WIP moy | WIP σ | "
            "Nervosité | Rupture % | Recovery j |"
        )
        lines.append("|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|")
        for tag, _ in CONFIGS:
            m = a.get(tag)
            if not m:
                continue
            lines.append(
                f"| {tag} | {m['otif_mean']:.3f} | "
                f"{m['q_mean']:.3f} | {m['d_mean']:.3f} | "
                f"{m['cost_per_u_mean']:.2f} | "
                f"{m['wip_avg_mean']:.2f} | "
                f"{m['wip_sd_mean']:.2f} | "
                f"{m['nervousness_mean']:.3f} | "
                f"{m['rupture_pct_mean']:.1%} | "
                f"{m['recovery_days_mean']:.1f} |"
            )
        lines.append("")
        # Diffs
        of = a.get("OF", {})
        ofe = a.get("OF+EVENT", {})
        bce = a.get("OF+EVENT+BCE", {})
        lines.append("### Différentiels")
        lines.append("")
        if of and ofe:
            lines.append(
                f"- OF+EVENT vs OF : "
                f"ΔOTIF {(ofe.get('otif_mean',0) - of.get('otif_mean',0)):+.3f}, "
                f"Δ€/u {(ofe.get('cost_per_u_mean',0) - of.get('cost_per_u_mean',0)):+.2f}, "
                f"Δnervosité {(ofe.get('nervousness_mean',0) - of.get('nervousness_mean',0)):+.3f}"
            )
        if ofe and bce:
            lines.append(
                f"- OF+EVENT+BCE vs OF+EVENT : "
                f"ΔOTIF {(bce.get('otif_mean',0) - ofe.get('otif_mean',0)):+.3f}, "
                f"Δ€/u {(bce.get('cost_per_u_mean',0) - ofe.get('cost_per_u_mean',0)):+.2f}, "
                f"Δnervosité {(bce.get('nervousness_mean',0) - ofe.get('nervousness_mean',0)):+.3f}"
            )
        lines.append("")

    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"OK {REPORT_MD}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=N_SEEDS_DEFAULT)
    args = parser.parse_args()
    seeds = list(range(900, 900 + args.seeds))
    n_total = len(seeds) * len(CONFIGS) * len(STRESS_LEVELS)
    print(f"=== Étude OF / OF+EVENT / OF+EVENT+BCE — stress faible + moyen ===")
    print(f"{len(STRESS_LEVELS)} niveaux × {len(CONFIGS)} configs × "
          f"{len(seeds)} seeds = {n_total} runs")

    all_runs: list[dict] = []
    with TemporaryDirectory(prefix="of_event_bce_") as tmp:
        work = Path(tmp)
        fix_dir = work / "fix"
        generate_random_fixtures(DEFAULT_SPEC, seed=42, out_dir=fix_dir)

        done = crashed = 0
        for level, cfg in STRESS_LEVELS:
            spec = RandomScenarioSpec(
                n_hazards=cfg["n_hazards"],
                n_sales_orders=cfg["n_sales_orders"],
                horizon_days=cfg["horizon_days"],
            )
            for seed in seeds:
                scen = generate_random_scenario(spec, seed=seed,
                                                 fixtures_dir=fix_dir)
                for tag, doctrine in CONFIGS:
                    r = _run_one(scen, doctrine, work, fix_dir, tag, level)
                    all_runs.append(r)
                    done += 1
                    if r.get("status") == "crashed":
                        crashed += 1
                    if done % 5 == 0 or done == n_total:
                        print(f"  ... {done}/{n_total} ({crashed} crashs)")

    fields = sorted({k for r in all_runs for k in r.keys()})
    with RUNS_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in all_runs:
            w.writerow(r)
    print(f"OK {RUNS_CSV}")

    agg = _agg(all_runs)
    SUMMARY_JSON.write_text(json.dumps(agg, indent=2, default=str))
    print(f"OK {SUMMARY_JSON}")
    _write_report(agg)

    print("\n=== Résumé ===")
    for level in ("faible", "moyen"):
        print(f"\n[stress {level}]")
        print(f"{'Config':<15} {'OTIF':>7} {'€/u':>7} "
              f"{'WIP σ':>7} {'Nerv':>7} {'Rupt%':>7} {'Rec j':>6}")
        a = agg.get(level, {})
        for tag, _ in CONFIGS:
            m = a.get(tag, {})
            if not m:
                continue
            print(f"{tag:<15} {m['otif_mean']:>7.3f} "
                  f"{m['cost_per_u_mean']:>7.2f} "
                  f"{m['wip_sd_mean']:>7.2f} "
                  f"{m['nervousness_mean']:>7.3f} "
                  f"{m['rupture_pct_mean']:>7.1%} "
                  f"{m['recovery_days_mean']:>6.1f}")
    return 0 if crashed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
