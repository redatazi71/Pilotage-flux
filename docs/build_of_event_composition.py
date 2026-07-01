"""Étude de composition incrémentale de la gestion événementielle.

Complète l'ablation leave-one-out (`build_of_event_ablation.py`) par
une approche additive : on part d'OF pur et on ajoute une brique à
la fois pour mesurer le gain marginal.

Configurations (chaque étape ajoute UN composant) :

  1. OF pur                — DOCTRINE_OF (aucun event sourcing)
  2. OF+capture            — OF+EVENT avec tous filtres désactivés
                             (événements capturés, aucune action)
  3. + CPM absorption      — cpm_margin_minutes default
  4. + Filtre tolérance    — tolerance_threshold_* défaut
  5. + Skip-latency V13.C  — enable_dual_memory_skip_latency = 1

Le passage 1 → 5 correspond à la construction incrémentale de la
gestion événementielle complète.

Stress fort (60j × 8 hazards), 5 seeds.

Sortie :
  - docs/of_event_composition_runs.csv
  - docs/of_event_composition_summary.json
  - docs/of_event_composition_report.md
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
    DOCTRINE_OF, DOCTRINE_OF_EVENT,
)
from pilotage_flux.data_factory import DEFAULT_SPEC, generate_random_fixtures


HORIZON_DAYS = 60
N_HAZARDS = 8
N_SEEDS = 5

TOL_DISABLE = {
    ("global", None, "tolerance_threshold_watch"): 999.0,
    ("global", None, "tolerance_threshold_correct_local"): 999.0,
    ("global", None, "tolerance_threshold_replan_local"): 999.0,
    ("global", None, "tolerance_threshold_escalate"): 999.0,
    ("global", None, "tolerance_threshold_replan_global"): 999.0,
}

# Composition additive : chaque config ajoute un composant
COMPOSITIONS: list[tuple[str, str, dict | None]] = [
    ("1_OF_pur", DOCTRINE_OF, None),
    ("2_+capture_only", DOCTRINE_OF_EVENT, {
        **TOL_DISABLE,
        ("global", None, "cpm_margin_minutes"): 0.0,
    }),
    ("3_+CPM_absorption", DOCTRINE_OF_EVENT, {
        **TOL_DISABLE,
    }),
    ("4_+tolerance_filter", DOCTRINE_OF_EVENT, None),
    ("5_+skip_latency_V13C", DOCTRINE_OF_EVENT, {
        ("global", None, "enable_dual_memory_skip_latency"): 1.0,
    }),
]

HERE = Path(__file__).resolve().parent
RUNS_CSV = HERE / "of_event_composition_runs.csv"
SUMMARY_JSON = HERE / "of_event_composition_summary.json"
REPORT_MD = HERE / "of_event_composition_report.md"


def _run_one(scen, work, fix_dir, tag, doctrine, overrides) -> dict:
    db = work / f"{tag}_{scen.seed}.db"
    try:
        kwargs = {"fixtures_dir": fix_dir}
        if overrides is not None:
            kwargs["param_overrides"] = overrides
        result = run_doctrine(scen, doctrine, db, **kwargs)
        k = compute_kpis(scen, result)
        wip_vals = list(result.daily_wip.values())
        wip_sd = statistics.stdev(wip_vals) if len(wip_vals) >= 2 else 0.0
        rupture_pct = (
            k.so_rejected / k.so_total if k.so_total > 0 else 0.0
        )
        first_hazard_day = min((h.day for h in scen.hazards), default=3)
        recovery_days = compute_time_to_recover(result, shock_day=first_hazard_day)
        return {
            "composition": tag,
            "doctrine": doctrine,
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
            "composition": tag, "doctrine": doctrine, "seed": scen.seed,
            "status": "crashed", "error": str(e)[:120],
        }


def _agg(runs: list[dict]) -> dict:
    from collections import defaultdict
    by_tag: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        if r.get("status") == "ok":
            by_tag[r["composition"]].append(r)

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
        "# Étude de composition incrémentale de la gestion événementielle",
        "",
        f"Stress fort ({HORIZON_DAYS}j × {N_HAZARDS} hazards), "
        f"{N_SEEDS} seeds par configuration.",
        "",
        "Chaque étape ajoute un composant à la précédente.",
        "",
        "## Tableau KPIs par composition",
        "",
        "| Étape | OTIF | Q | D | €/u | WIP moy | WIP σ | Nervosité | Rupture % | Recovery j |",
        "|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|",
    ]
    for tag, _, _ in COMPOSITIONS:
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
    lines.append("## Gain marginal cumulé (vs étape précédente)")
    lines.append("")
    lines.append("| Étape | ΔOTIF | Δ€/u | ΔNervosité | Δrupture | Δrecovery |")
    lines.append("|---|:-:|:-:|:-:|:-:|:-:|")
    prev = None
    for tag, _, _ in COMPOSITIONS:
        a = agg.get(tag)
        if not a:
            continue
        if prev is None:
            lines.append(f"| {tag} | (référence) | | | | |")
        else:
            lines.append(
                f"| {tag} | "
                f"{(a['otif_mean'] - prev['otif_mean']):+.3f} | "
                f"{(a['cost_per_u_mean'] - prev['cost_per_u_mean']):+.2f} | "
                f"{(a['nervousness_mean'] - prev['nervousness_mean']):+.3f} | "
                f"{(a['rupture_pct_mean'] - prev['rupture_pct_mean']):+.1%} | "
                f"{(a['recovery_days_mean'] - prev['recovery_days_mean']):+.1f}j |"
            )
        prev = a

    lines.append("")
    lines.append("## Gain cumulé (vs OF pur)")
    lines.append("")
    baseline = agg.get("1_OF_pur")
    if baseline:
        lines.append(
            "| Étape | ΔOTIF | Δ€/u | ΔNervosité | Δrupture | Δrecovery |"
        )
        lines.append("|---|:-:|:-:|:-:|:-:|:-:|")
        for tag, _, _ in COMPOSITIONS:
            if tag == "1_OF_pur":
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
    seeds = list(range(2000, 2000 + N_SEEDS))
    n_total = len(seeds) * len(COMPOSITIONS)
    print(f"=== Composition additive OF → OF+EVENT complet — "
          f"{len(COMPOSITIONS)} étapes × {len(seeds)} seeds = "
          f"{n_total} runs ===")

    all_runs: list[dict] = []
    with TemporaryDirectory(prefix="ofe_comp_") as tmp:
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
            for tag, doctrine, overrides in COMPOSITIONS:
                r = _run_one(scen, work, fix_dir, tag, doctrine, overrides)
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

    print("\n=== Résumé composition ===")
    print(f"{'Étape':<25} {'OTIF':>7} {'€/u':>7} "
          f"{'WIP σ':>7} {'Nerv':>7} {'Rupt%':>7} {'Rec j':>6}")
    for tag, _, _ in COMPOSITIONS:
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
