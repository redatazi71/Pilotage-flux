"""Étude de sensibilité à la saturation goulot (ρ).

Objectif : illustrer l'effondrement à ρ_bottleneck ≥ 0.95 et
visualiser le "sweet spot" ρ ≈ 0.85 (Goldratt/TOC).

Configurations OF+EVENT avec toc_target_saturation ∈ :
  {0.60, 0.70, 0.80, 0.85, 0.90, 0.95, 1.00}

Stress moyen (45j × 5 hazards), 5 seeds.

Sortie :
  - docs/of_event_saturation_runs.csv
  - docs/of_event_saturation_summary.json
  - docs/of_event_saturation_report.md
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


RHOS = [0.60, 0.70, 0.80, 0.85, 0.90, 0.95, 1.00]
N_SEEDS = 5

HERE = Path(__file__).resolve().parent
RUNS_CSV = HERE / "of_event_saturation_runs.csv"
SUMMARY_JSON = HERE / "of_event_saturation_summary.json"
REPORT_MD = HERE / "of_event_saturation_report.md"


def _run_one(scen, work, fix_dir, rho) -> dict:
    db = work / f"rho{int(rho*100):03d}_{scen.seed}.db"
    try:
        result = run_doctrine(
            scen, DOCTRINE_OF_EVENT, db, fixtures_dir=fix_dir,
            param_overrides={
                ("global", None, "toc_target_saturation"): rho,
            },
        )
        k = compute_kpis(scen, result)
        wip_vals = list(result.daily_wip.values())
        wip_sd = statistics.stdev(wip_vals) if len(wip_vals) >= 2 else 0.0
        rupture_pct = (
            k.so_rejected / k.so_total if k.so_total > 0 else 0.0
        )
        first_hazard_day = min((h.day for h in scen.hazards), default=3)
        recovery_days = compute_time_to_recover(result, shock_day=first_hazard_day)
        return {
            "rho": rho,
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
            "rho": rho, "seed": scen.seed,
            "status": "crashed", "error": str(e)[:120],
        }


def _agg(runs: list[dict]) -> dict:
    from collections import defaultdict
    by_rho: dict[float, list[dict]] = defaultdict(list)
    for r in runs:
        if r.get("status") == "ok":
            by_rho[r["rho"]].append(r)

    def mean(rs, key):
        vals = [r.get(key, 0.0) for r in rs]
        return statistics.mean(vals) if vals else 0.0

    out: dict[str, dict] = {}
    for rho, rs in by_rho.items():
        out[f"{rho:.2f}"] = {
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
        "# Sensibilité à la saturation goulot (ρ)",
        "",
        f"Stress moyen (45j × 5 hazards), OF+EVENT, {N_SEEDS} seeds par ρ.",
        "",
        "| ρ cible | OTIF | €/u | WIP moy | WIP σ | Nervosité | Rupture % | Recovery j |",
        "|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|",
    ]
    for rho in RHOS:
        a = agg.get(f"{rho:.2f}")
        if not a:
            continue
        lines.append(
            f"| {rho:.2f} | {a['otif_mean']:.3f} | "
            f"{a['cost_per_u_mean']:.2f} | "
            f"{a['wip_avg_mean']:.2f} | {a['wip_sd_mean']:.2f} | "
            f"{a['nervousness_mean']:.3f} | "
            f"{a['rupture_pct_mean']:.1%} | "
            f"{a['recovery_days_mean']:.1f} |"
        )
    lines.append("")
    lines.append("## Interprétation")
    lines.append("")
    lines.append("- ρ < 0.85 : sous-utilisation ; coût unitaire élevé")
    lines.append("- ρ ≈ 0.85 : sweet spot Goldratt/TOC")
    lines.append("- ρ > 0.90 : dégradation (files croissent)")
    lines.append("- ρ = 1.00 : saturation totale ; effondrement attendu")
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"OK {REPORT_MD}")


def main() -> int:
    seeds = list(range(3000, 3000 + N_SEEDS))
    n_total = len(seeds) * len(RHOS)
    print(f"=== Sensibilité ρ — {len(RHOS)} niveaux × "
          f"{len(seeds)} seeds = {n_total} runs ===")

    all_runs: list[dict] = []
    with TemporaryDirectory(prefix="ofe_sat_") as tmp:
        work = Path(tmp)
        fix_dir = work / "fix"
        generate_random_fixtures(DEFAULT_SPEC, seed=42, out_dir=fix_dir)
        spec = RandomScenarioSpec(
            n_hazards=5, n_sales_orders=12, horizon_days=45,
        )

        done = crashed = 0
        for seed in seeds:
            scen = generate_random_scenario(spec, seed=seed,
                                             fixtures_dir=fix_dir)
            for rho in RHOS:
                r = _run_one(scen, work, fix_dir, rho)
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

    print("\n=== Résumé ρ ===")
    print(f"{'ρ':>6} {'OTIF':>7} {'€/u':>7} {'WIP σ':>7} "
          f"{'Nerv':>7} {'Rupt%':>7} {'Rec j':>6}")
    for rho in RHOS:
        a = agg.get(f"{rho:.2f}")
        if not a:
            continue
        print(f"{rho:>6.2f} {a['otif_mean']:>7.3f} "
              f"{a['cost_per_u_mean']:>7.2f} "
              f"{a['wip_sd_mean']:>7.2f} "
              f"{a['nervousness_mean']:>7.3f} "
              f"{a['rupture_pct_mean']:>7.1%} "
              f"{a['recovery_days_mean']:>6.1f}")
    return 0 if crashed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
