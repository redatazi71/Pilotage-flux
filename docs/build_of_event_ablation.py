"""Étude d'ablation sur les composants de la gestion événementielle.

Baseline : OF+EVENT (défauts actuels).
Ablations : désactivation par overrides paramétriques successifs.

Configurations :
  1. baseline            : OF+EVENT défauts
  2. +skip_latency (V13.C): dual memory shortcut activé
  3. -CPM_absorption      : cpm_margin_minutes = 0
  4. -tolerance_filter    : tolerance_threshold_* = 999 (toutes → inform)
  5. -all_filters         : CPM=0 ET tolerance>999

Stress fort (60j × 8 hazards), 5 seeds, OF+EVENT uniquement.

Sortie :
  - docs/of_event_ablation_runs.csv
  - docs/of_event_ablation_summary.json
  - docs/of_event_ablation_report.md
"""
from __future__ import annotations

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
from pilotage_flux.comparative.scenario import DOCTRINE_OF_EVENT
from pilotage_flux.data_factory import DEFAULT_SPEC, generate_random_fixtures


HORIZON_DAYS = 60
N_HAZARDS = 8
N_SEEDS = 5

# Overrides par ablation ; None = pas d'override (défauts)
ABLATIONS: list[tuple[str, dict | None]] = [
    ("baseline", None),
    ("+skip_latency_V13C", {
        ("global", None, "enable_dual_memory_skip_latency"): 1.0,
    }),
    ("-CPM_absorption", {
        ("global", None, "cpm_margin_minutes"): 0.0,
    }),
    ("-tolerance_filter", {
        ("global", None, "tolerance_threshold_watch"): 999.0,
        ("global", None, "tolerance_threshold_correct_local"): 999.0,
        ("global", None, "tolerance_threshold_replan_local"): 999.0,
        ("global", None, "tolerance_threshold_escalate"): 999.0,
        ("global", None, "tolerance_threshold_replan_global"): 999.0,
    }),
    ("-all_filters", {
        ("global", None, "cpm_margin_minutes"): 0.0,
        ("global", None, "tolerance_threshold_watch"): 999.0,
        ("global", None, "tolerance_threshold_correct_local"): 999.0,
        ("global", None, "tolerance_threshold_replan_local"): 999.0,
        ("global", None, "tolerance_threshold_escalate"): 999.0,
        ("global", None, "tolerance_threshold_replan_global"): 999.0,
    }),
]

HERE = Path(__file__).resolve().parent
RUNS_CSV = HERE / "of_event_ablation_runs.csv"
SUMMARY_JSON = HERE / "of_event_ablation_summary.json"
REPORT_MD = HERE / "of_event_ablation_report.md"


def _run_one(scen, work, fix_dir, tag, overrides) -> dict:
    db = work / f"{tag}_{scen.seed}.db"
    try:
        kwargs = {"fixtures_dir": fix_dir}
        if overrides is not None:
            kwargs["param_overrides"] = overrides
        result = run_doctrine(scen, DOCTRINE_OF_EVENT, db, **kwargs)
        k = compute_kpis(scen, result)
        wip_vals = list(result.daily_wip.values())
        wip_sd = statistics.stdev(wip_vals) if len(wip_vals) >= 2 else 0.0
        rupture_pct = (
            k.so_rejected / k.so_total if k.so_total > 0 else 0.0
        )
        first_hazard_day = min((h.day for h in scen.hazards), default=3)
        recovery_days = compute_time_to_recover(result, shock_day=first_hazard_day)
        return {
            "ablation": tag,
            "seed": scen.seed,
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
            "ablation": tag, "seed": scen.seed,
            "status": "crashed", "error": str(e)[:120],
        }


def _agg(runs: list[dict]) -> dict:
    from collections import defaultdict
    by_tag: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        if r.get("status") == "ok":
            by_tag[r["ablation"]].append(r)

    def mean(rs, key):
        vals = [r.get(key, 0.0) for r in rs]
        return statistics.mean(vals) if vals else 0.0

    out: dict[str, dict] = {}
    for tag, rs in by_tag.items():
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
        }
    return out


def _write_report(agg: dict) -> None:
    lines = [
        "# Étude d'ablation — composants de la gestion événementielle (OF+EVENT)",
        "",
        f"Stress fort ({HORIZON_DAYS}j × {N_HAZARDS} hazards), "
        f"{N_SEEDS} seeds par ablation.",
        "",
        "## Tableau KPIs par ablation",
        "",
        "| Ablation | OTIF | Q | D | €/u | WIP moy | WIP σ | Nervosité | Rupture % | Recovery j |",
        "|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|",
    ]
    for tag, _ in ABLATIONS:
        a = agg.get(tag)
        if not a:
            continue
        lines.append(
            f"| {tag} | {a['otif_mean']:.3f} | {a['q_mean']:.3f} | "
            f"{a['d_mean']:.3f} | {a['cost_per_u_mean']:.2f} | "
            f"{a['wip_avg_mean']:.2f} | {a['wip_sd_mean']:.2f} | "
            f"{a['nervousness_mean']:.3f} | "
            f"{a['rupture_pct_mean']:.1%} | "
            f"{a['recovery_days_mean']:.1f} |"
        )
    lines.append("")
    lines.append("## Contribution isolée par composant (vs baseline)")
    lines.append("")
    baseline = agg.get("baseline")
    if baseline:
        lines.append(
            "| Ablation | ΔOTIF | Δ€/u | ΔNervosité | Δrupture | Δrecovery |"
        )
        lines.append("|---|:-:|:-:|:-:|:-:|:-:|")
        for tag, _ in ABLATIONS:
            if tag == "baseline":
                continue
            a = agg.get(tag)
            if not a:
                continue
            lines.append(
                f"| {tag} | "
                f"{(a['otif_mean'] - baseline['otif_mean']):+.3f} | "
                f"{(a['cost_per_u_mean'] - baseline['cost_per_u_mean']):+.2f} | "
                f"{(a['nervousness_mean'] - baseline['nervousness_mean']):+.3f} | "
                f"{(a['rupture_pct_mean'] - baseline['rupture_pct_mean']):+.1%} | "
                f"{(a['recovery_days_mean'] - baseline['recovery_days_mean']):+.1f}j |"
            )

    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"OK {REPORT_MD}")


def main() -> int:
    seeds = list(range(1500, 1500 + N_SEEDS))
    n_total = len(seeds) * len(ABLATIONS)
    print(f"=== Ablation OF+EVENT — {len(ABLATIONS)} configs × "
          f"{len(seeds)} seeds = {n_total} runs ===")

    all_runs: list[dict] = []
    with TemporaryDirectory(prefix="ofe_abl_") as tmp:
        work = Path(tmp)
        fix_dir = work / "fix"
        generate_random_fixtures(DEFAULT_SPEC, seed=42, out_dir=fix_dir)
        spec = RandomScenarioSpec(
            n_hazards=N_HAZARDS,
            n_sales_orders=15,
            horizon_days=HORIZON_DAYS,
        )

        done = crashed = 0
        for seed in seeds:
            scen = generate_random_scenario(spec, seed=seed,
                                             fixtures_dir=fix_dir)
            for tag, overrides in ABLATIONS:
                r = _run_one(scen, work, fix_dir, tag, overrides)
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

    print("\n=== Résumé ablation OF+EVENT ===")
    print(f"{'Ablation':<25} {'OTIF':>7} {'€/u':>7} "
          f"{'WIP σ':>7} {'Nerv':>7} {'Rupt%':>7} {'Rec j':>6}")
    for tag, _ in ABLATIONS:
        a = agg.get(tag)
        if not a:
            continue
        print(f"{tag:<25} {a['otif_mean']:>7.3f} "
              f"{a['cost_per_u_mean']:>7.2f} "
              f"{a['wip_sd_mean']:>7.2f} "
              f"{a['nervousness_mean']:>7.3f} "
              f"{a['rupture_pct_mean']:>7.1%} "
              f"{a['recovery_days_mean']:>6.1f}")
    return 0 if crashed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
