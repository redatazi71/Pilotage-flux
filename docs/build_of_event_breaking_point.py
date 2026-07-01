"""Étude « breaking point » OF+EVENT.

Objectifs :
  1. Identifier la limite où OF+EVENT casse (OTIF < 90%, rupture > 5%
     ou recovery > 30j).
  2. Mesurer le temps de retour à la normale (recovery) à chaque
     niveau de stress.
  3. Consolider OTIF, WIP (moyen + σ), coût unitaire, nervosité,
     rupture sur 4 niveaux nommés + 3 niveaux au-delà.

Gradient de stress :

  | Niveau      | Horizon | N hazards | Severity | N SOs |
  |-------------|---------|-----------|----------|-------|
  | faible      | 30j     | 3         | ×1       | 8     |
  | moyen       | 45j     | 5         | ×1       | 12    |
  | fort        | 60j     | 8         | ×1       | 15    |
  | extrême     | 120j    | 20        | ×1       | 25    |
  | extrême+    | 150j    | 30        | ×1.3     | 30    |
  | rupture     | 180j    | 40        | ×1.6     | 35    |
  | rupture++   | 240j    | 60        | ×2.0     | 45    |

Sortie :
  - docs/of_event_breaking_point_runs.csv
  - docs/of_event_breaking_point_summary.json
  - docs/of_event_breaking_point_report.md
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


def _spec(horizon: int, n_haz: int, n_so: int, severity: float,
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
        breakdown_duration_range=(
            int(2 * severity), int(5 * severity)),
        breakdown_factor_range=(1.5 * severity, 3.0 * severity),
        nc_scrap_range=(int(10 * severity), int(25 * severity)),
        po_delay_range=(int(3 * severity), int(10 * severity)),
        urgent_qty_range=(int(20 * severity), int(60 * severity)),
        logistic_block_range=(int(2 * severity), int(4 * severity)),
    )


LEVELS = [
    ("faible",     30,  3,  8, 1.0, False),
    ("moyen",      45,  5, 12, 1.0, False),
    ("fort",       60,  8, 15, 1.0, False),
    ("extrême",   120, 20, 25, 1.0, True),
    ("extrême+",  150, 30, 30, 1.3, True),
    ("rupture",   180, 40, 35, 1.6, True),
    ("rupture++", 240, 60, 45, 2.0, True),
]

N_SEEDS = 5

HERE = Path(__file__).resolve().parent
RUNS_CSV = HERE / "of_event_breaking_point_runs.csv"
SUMMARY_JSON = HERE / "of_event_breaking_point_summary.json"
REPORT_MD = HERE / "of_event_breaking_point_report.md"


def _run_one(scen, work, fix_dir, level) -> dict:
    db = work / f"{level}_{scen.seed}.db"
    try:
        result = run_doctrine(
            scen, DOCTRINE_OF_EVENT, db, fixtures_dir=fix_dir,
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
            "level": level,
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
            "level": level, "seed": scen.seed,
            "status": "crashed", "error": str(e)[:120],
        }


def _agg(runs: list[dict]) -> dict:
    from collections import defaultdict
    by_level: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        if r.get("status") == "ok":
            by_level[r["level"]].append(r)

    def mean(rs, key):
        vals = [r.get(key, 0.0) for r in rs]
        return statistics.mean(vals) if vals else 0.0

    out: dict[str, dict] = {}
    for level, rs in by_level.items():
        out[level] = {
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


def _breaking_diagnosis(a: dict) -> str:
    reasons = []
    if a["otif_mean"] < 0.90:
        reasons.append(f"OTIF {a['otif_mean']:.3f} < 0.90")
    if a["rupture_pct_mean"] > 0.05:
        reasons.append(f"rupture {a['rupture_pct_mean']:.1%} > 5%")
    if a["recovery_days_mean"] > 30:
        reasons.append(f"recovery {a['recovery_days_mean']:.1f}j > 30j")
    if not reasons:
        return "TENU"
    return " ; ".join(reasons)


def _write_report(agg: dict) -> None:
    lines = [
        "# Étude « breaking point » OF+EVENT",
        "",
        f"Gradient de stress sur 7 niveaux, {N_SEEDS} seeds par niveau, "
        f"doctrine OF+EVENT uniquement.",
        "",
        "## Tableau consolidé QCDS + WIP + rupture + recovery",
        "",
        "| Niveau | OTIF | Q | D | €/u | WIP moy | WIP σ | Nervosité | Rupture % | Recovery j | Diagnostic |",
        "|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|---|",
    ]
    for level, _, _, _, _, _ in LEVELS:
        a = agg.get(level)
        if not a:
            continue
        diag = _breaking_diagnosis(a)
        lines.append(
            f"| {level} | {a['otif_mean']:.3f} | {a['q_mean']:.3f} | "
            f"{a['d_mean']:.3f} | {a['cost_per_u_mean']:.2f} | "
            f"{a['wip_avg_mean']:.2f} | {a['wip_sd_mean']:.2f} | "
            f"{a['nervousness_mean']:.3f} | "
            f"{a['rupture_pct_mean']:.1%} | "
            f"{a['recovery_days_mean']:.1f} | {diag} |"
        )
    lines.append("")
    lines.append("## Seuils de casse")
    lines.append("")
    lines.append("Le système est considéré cassé si l'un des critères est franchi :")
    lines.append("")
    lines.append("- OTIF < 0.90")
    lines.append("- Rupture > 5%")
    lines.append("- Recovery > 30 jours")
    lines.append("")
    lines.append("## Progression stress → dégradation")
    lines.append("")
    lines.append("| Niveau | ΔOTIF vs faible | Δrupture vs faible | Δrecovery vs faible |")
    lines.append("|---|:-:|:-:|:-:|")
    baseline = agg.get("faible")
    if baseline:
        for level, _, _, _, _, _ in LEVELS:
            a = agg.get(level)
            if not a:
                continue
            d_otif = a["otif_mean"] - baseline["otif_mean"]
            d_rup = a["rupture_pct_mean"] - baseline["rupture_pct_mean"]
            d_rec = a["recovery_days_mean"] - baseline["recovery_days_mean"]
            lines.append(
                f"| {level} | {d_otif:+.3f} | {d_rup:+.1%} | "
                f"{d_rec:+.1f}j |"
            )
    lines.append("")
    lines.append("## Conclusion")
    lines.append("")
    tenu = [level for level, _, _, _, _, _ in LEVELS
            if agg.get(level) and _breaking_diagnosis(agg[level]) == "TENU"]
    casse = [level for level, _, _, _, _, _ in LEVELS
             if agg.get(level) and _breaking_diagnosis(agg[level]) != "TENU"]
    if tenu:
        lines.append(f"- Niveaux TENUS : {', '.join(tenu)}")
    if casse:
        lines.append(f"- Niveaux CASSÉS : {', '.join(casse)}")
        first_break = casse[0]
        lines.append(f"- **Premier point de rupture : {first_break}**")

    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"OK {REPORT_MD}")


def main() -> int:
    seeds = list(range(1000, 1000 + N_SEEDS))
    n_total = len(seeds) * len(LEVELS)
    print(f"=== Breaking point OF+EVENT — {len(LEVELS)} niveaux × "
          f"{len(seeds)} seeds = {n_total} runs ===")

    all_runs: list[dict] = []
    with TemporaryDirectory(prefix="ofe_break_") as tmp:
        work = Path(tmp)
        fix_dir = work / "fix"
        generate_random_fixtures(DEFAULT_SPEC, seed=42, out_dir=fix_dir)

        done = crashed = 0
        for level, horizon, n_haz, n_so, sev, five in LEVELS:
            spec = _spec(horizon, n_haz, n_so, sev, five_types=five)
            for seed in seeds:
                scen = generate_random_scenario(spec, seed=seed,
                                                fixtures_dir=fix_dir)
                r = _run_one(scen, work, fix_dir, level)
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
    print(f"{'Niveau':<12} {'OTIF':>7} {'€/u':>7} {'WIP σ':>7} "
          f"{'Nerv':>7} {'Rupt%':>7} {'Rec j':>6}  Diagnostic")
    for level, _, _, _, _, _ in LEVELS:
        a = agg.get(level)
        if not a:
            continue
        diag = _breaking_diagnosis(a)
        print(f"{level:<12} {a['otif_mean']:>7.3f} "
              f"{a['cost_per_u_mean']:>7.2f} "
              f"{a['wip_sd_mean']:>7.2f} "
              f"{a['nervousness_mean']:>7.3f} "
              f"{a['rupture_pct_mean']:>7.1%} "
              f"{a['recovery_days_mean']:>6.1f}  {diag}")
    return 0 if crashed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
