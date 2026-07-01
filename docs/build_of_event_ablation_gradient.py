"""Ablation OF+EVENT × gradient de stress (4 niveaux).

Trance l'hypothèse : les briques (CPM absorption, V13.C skip-latency)
qui semblent nulles au stress fort le sont-elles à tous les niveaux ?

Configurations d'ablation (identiques à build_of_event_ablation.py) :
  1. baseline
  2. +skip_latency_V13C
  3. -CPM_absorption
  4. -tolerance_filter
  5. -all_filters

Niveaux de stress :
  - faible   : 30j × 3 hazards
  - moyen    : 45j × 5 hazards
  - fort     : 60j × 8 hazards
  - extrême  : 120j × 20 hazards × 5 types (+ severity légèrement)

5 seeds par (niveau × ablation) = 5 × 4 × 5 = 100 runs

Sortie :
  - docs/of_event_ablation_gradient_runs.csv
  - docs/of_event_ablation_gradient_summary.json
  - docs/of_event_ablation_gradient_report.md
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
from pilotage_flux.comparative.scenario import (
    DOCTRINE_OF_EVENT,
    HAZARD_BREAKDOWN,
    HAZARD_LOGISTIC_DELAY,
    HAZARD_PO_DELAY,
    HAZARD_QUALITY_NC,
    HAZARD_URGENT_ORDER,
)
from pilotage_flux.data_factory import DEFAULT_SPEC, generate_random_fixtures


N_SEEDS = 5

TOL_DISABLE = {
    ("global", None, "tolerance_threshold_watch"): 999.0,
    ("global", None, "tolerance_threshold_correct_local"): 999.0,
    ("global", None, "tolerance_threshold_replan_local"): 999.0,
    ("global", None, "tolerance_threshold_escalate"): 999.0,
    ("global", None, "tolerance_threshold_replan_global"): 999.0,
}

ABLATIONS: list[tuple[str, dict | None]] = [
    ("baseline", None),
    ("+skip_latency_V13C", {
        ("global", None, "enable_dual_memory_skip_latency"): 1.0,
    }),
    ("-CPM_absorption", {
        ("global", None, "cpm_margin_minutes"): 0.0,
    }),
    ("-tolerance_filter", dict(TOL_DISABLE)),
    ("-all_filters", {
        ("global", None, "cpm_margin_minutes"): 0.0,
        **TOL_DISABLE,
    }),
]


def _spec(horizon: int, n_haz: int, n_so: int,
          five_types: bool = False) -> RandomScenarioSpec:
    kinds = [
        HAZARD_BREAKDOWN, HAZARD_QUALITY_NC,
        HAZARD_PO_DELAY, HAZARD_URGENT_ORDER,
    ]
    weights = {
        HAZARD_BREAKDOWN: 0.30, HAZARD_QUALITY_NC: 0.30,
        HAZARD_PO_DELAY: 0.20, HAZARD_URGENT_ORDER: 0.20,
    }
    if five_types:
        kinds.append(HAZARD_LOGISTIC_DELAY)
        weights = {
            HAZARD_BREAKDOWN: 0.28, HAZARD_QUALITY_NC: 0.24,
            HAZARD_PO_DELAY: 0.20, HAZARD_URGENT_ORDER: 0.16,
            HAZARD_LOGISTIC_DELAY: 0.12,
        }
    return RandomScenarioSpec(
        n_hazards=n_haz,
        n_sales_orders=n_so,
        horizon_days=horizon,
        hazard_kinds=kinds,
        hazard_weights=weights,
    )


LEVELS = [
    ("faible",   30,  3,  8, False),
    ("moyen",    45,  5, 12, False),
    ("fort",     60,  8, 15, False),
    ("extrême", 120, 20, 25, True),
]

HERE = Path(__file__).resolve().parent
RUNS_CSV = HERE / "of_event_ablation_gradient_runs.csv"
SUMMARY_JSON = HERE / "of_event_ablation_gradient_summary.json"
REPORT_MD = HERE / "of_event_ablation_gradient_report.md"


def _run_one(scen, work, fix_dir, level, ablation, overrides) -> dict:
    db = work / f"{level}_{ablation}_{scen.seed}.db"
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
            "level": level,
            "ablation": ablation,
            "seed": scen.seed,
            "status": "ok",
            "otif": k.quantity_compliance * k.disponibility_so_level,
            "cost_per_u": k.cost_per_unit_delivered,
            "wip_avg": k.wip_avg,
            "wip_sd": wip_sd,
            "nervousness": k.nervousness,
            "so_total": k.so_total,
            "so_rejected": k.so_rejected,
            "rupture_pct": rupture_pct,
            "recovery_days": recovery_days,
        }
    except Exception as e:
        return {
            "level": level, "ablation": ablation, "seed": scen.seed,
            "status": "crashed", "error": str(e)[:120],
        }


def _agg(runs: list[dict]) -> dict:
    from collections import defaultdict
    by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in runs:
        if r.get("status") == "ok":
            by_key[(r["level"], r["ablation"])].append(r)

    def mean(rs, key):
        vals = [r.get(key, 0.0) for r in rs]
        return statistics.mean(vals) if vals else 0.0

    out: dict[str, dict[str, dict]] = {}
    for (level, ab), rs in by_key.items():
        out.setdefault(level, {})[ab] = {
            "n_runs": len(rs),
            "otif_mean": mean(rs, "otif"),
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
        "# Ablation OF+EVENT × gradient de stress (4 niveaux)",
        "",
        f"5 configurations d'ablation × 4 niveaux de stress × "
        f"{N_SEEDS} seeds = 100 runs.",
        "",
        "Objectif : tester si les briques nulles à stress fort le sont "
        "à tous les niveaux (CPM absorption, V13.C skip-latency).",
        "",
    ]
    for level, _, _, _, _ in LEVELS:
        a_level = agg.get(level, {})
        if not a_level:
            continue
        lines.append(f"## Stress {level.upper()}")
        lines.append("")
        lines.append(
            "| Ablation | OTIF | €/u | WIP σ | Nervosité | Rupture % | Recovery j |"
        )
        lines.append("|---|:-:|:-:|:-:|:-:|:-:|:-:|")
        for ab, _ in ABLATIONS:
            m = a_level.get(ab)
            if not m:
                continue
            lines.append(
                f"| {ab} | {m['otif_mean']:.3f} | "
                f"{m['cost_per_u_mean']:.2f} | "
                f"{m['wip_sd_mean']:.2f} | "
                f"{m['nervousness_mean']:.3f} | "
                f"{m['rupture_pct_mean']:.1%} | "
                f"{m['recovery_days_mean']:.1f} |"
            )
        # Diffs vs baseline
        base = a_level.get("baseline")
        if base:
            lines.append("")
            lines.append("Contribution isolée par composant (vs baseline) :")
            lines.append("")
            lines.append(
                "| Ablation | ΔOTIF | Δ€/u | ΔNervosité | Δrupture | Δrecovery |"
            )
            lines.append("|---|:-:|:-:|:-:|:-:|:-:|")
            for ab, _ in ABLATIONS:
                if ab == "baseline":
                    continue
                m = a_level.get(ab)
                if not m:
                    continue
                lines.append(
                    f"| {ab} | "
                    f"{(m['otif_mean'] - base['otif_mean']):+.3f} | "
                    f"{(m['cost_per_u_mean'] - base['cost_per_u_mean']):+.2f} | "
                    f"{(m['nervousness_mean'] - base['nervousness_mean']):+.3f} | "
                    f"{(m['rupture_pct_mean'] - base['rupture_pct_mean']):+.1%} | "
                    f"{(m['recovery_days_mean'] - base['recovery_days_mean']):+.1f}j |"
                )
        lines.append("")

    # Vue synthétique : pour chaque ablation, l'effet à chaque niveau
    lines.append("## Synthèse cross-niveaux")
    lines.append("")
    lines.append(
        "| Ablation | Δ€/u faible | Δ€/u moyen | Δ€/u fort | Δ€/u extrême |"
    )
    lines.append("|---|:-:|:-:|:-:|:-:|")
    for ab, _ in ABLATIONS:
        if ab == "baseline":
            continue
        row = f"| {ab} |"
        for level, _, _, _, _ in LEVELS:
            a_level = agg.get(level, {})
            base = a_level.get("baseline")
            m = a_level.get(ab)
            if base and m:
                d = m["cost_per_u_mean"] - base["cost_per_u_mean"]
                row += f" {d:+.2f} |"
            else:
                row += " — |"
        lines.append(row)

    lines.append("")
    lines.append(
        "| Ablation | Δnerv faible | Δnerv moyen | Δnerv fort | Δnerv extrême |"
    )
    lines.append("|---|:-:|:-:|:-:|:-:|")
    for ab, _ in ABLATIONS:
        if ab == "baseline":
            continue
        row = f"| {ab} |"
        for level, _, _, _, _ in LEVELS:
            a_level = agg.get(level, {})
            base = a_level.get("baseline")
            m = a_level.get(ab)
            if base and m:
                d = m["nervousness_mean"] - base["nervousness_mean"]
                row += f" {d:+.3f} |"
            else:
                row += " — |"
        lines.append(row)

    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"OK {REPORT_MD}")


def main() -> int:
    seeds = list(range(2500, 2500 + N_SEEDS))
    n_total = len(seeds) * len(ABLATIONS) * len(LEVELS)
    print(f"=== Ablation OF+EVENT × gradient — {len(LEVELS)} niveaux × "
          f"{len(ABLATIONS)} configs × {len(seeds)} seeds = "
          f"{n_total} runs ===")

    all_runs: list[dict] = []
    with TemporaryDirectory(prefix="ofe_abg_") as tmp:
        work = Path(tmp)
        fix_dir = work / "fix"
        generate_random_fixtures(DEFAULT_SPEC, seed=42, out_dir=fix_dir)

        done = crashed = 0
        for level, horizon, n_haz, n_so, five in LEVELS:
            spec = _spec(horizon, n_haz, n_so, five_types=five)
            for seed in seeds:
                scen = generate_random_scenario(spec, seed=seed,
                                                 fixtures_dir=fix_dir)
                for ab, overrides in ABLATIONS:
                    r = _run_one(scen, work, fix_dir, level, ab, overrides)
                    all_runs.append(r)
                    done += 1
                    if r.get("status") == "crashed":
                        crashed += 1
                    if done % 10 == 0 or done == n_total:
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

    print("\n=== Résumé cross-niveaux ===")
    for level, _, _, _, _ in LEVELS:
        a_level = agg.get(level, {})
        if not a_level:
            continue
        print(f"\n[stress {level}]")
        print(f"{'Ablation':<25} {'OTIF':>7} {'€/u':>7} "
              f"{'Nerv':>7} {'Rupt%':>7} {'Rec j':>6}")
        for ab, _ in ABLATIONS:
            m = a_level.get(ab)
            if not m:
                continue
            print(f"{ab:<25} {m['otif_mean']:>7.3f} "
                  f"{m['cost_per_u_mean']:>7.2f} "
                  f"{m['nervousness_mean']:>7.3f} "
                  f"{m['rupture_pct_mean']:>7.1%} "
                  f"{m['recovery_days_mean']:>6.1f}")
    return 0 if crashed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
