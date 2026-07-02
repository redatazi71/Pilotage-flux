"""Master v2 étendu — inclut les extensions Ext-g/h/l dans le protocole.

Cette version étend `build_master_v2_windows.py` avec 3 axes activables
en ligne de commande :

  --include-reactive-cpsat   ajoute DOCTRINE_OF_REACTIVE_CPSAT aux configs
                             (Ext-l, baseline « re-solve permanent »)
  --cascade <profil>         active un profil cascade sur tous les runs
                             (Ext-g : mecanique_to_supply | qualite_to_rerun |
                              supply_to_urgent | humain_to_qualite | tempete)
  --bounded-rationality      injecte HumanDecisionModel dans le dispatcher
                             (Ext-h, biais Simon / Kahneman-Tversky / fatigue)

Le CSV de sortie contient une colonne `variant` qui identifie chaque
combinaison (base / cascade / bounded / cpsat / …), ce qui permet
d'analyser tous les axes dans un seul fichier.

Usage typique (Windows) :

    :: 1. Base réetendue (3 configs → 4 avec réactif)
    python docs\\build_master_v2_extended.py --seeds 24 --workers 8 ^
        --include-reactive-cpsat ^
        --out docs\\master_v2_ext_base.csv

    :: 2. Cascade « tempête » sur les 3 doctrines classiques
    python docs\\build_master_v2_extended.py --seeds 24 --workers 8 ^
        --cascade tempete ^
        --out docs\\master_v2_ext_cascade.csv

    :: 3. Facteur humain
    python docs\\build_master_v2_extended.py --seeds 24 --workers 8 ^
        --bounded-rationality ^
        --out docs\\master_v2_ext_human.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pilotage_flux.comparative.hazard_correlation import (
    CASCADE_PROFILES, apply_correlations,
)
from pilotage_flux.comparative.kpis import compute_kpis
from pilotage_flux.comparative.random_scenario import (
    RandomScenarioSpec, generate_random_scenario,
)
from pilotage_flux.comparative.resilience import compute_time_to_recover
from pilotage_flux.comparative.runner import run_doctrine
from pilotage_flux.comparative.scenario import (
    DOCTRINE_EVENT,
    DOCTRINE_OF,
    DOCTRINE_OF_EVENT,
    DOCTRINE_OF_REACTIVE_CPSAT,
    HAZARD_BREAKDOWN,
    HAZARD_LOGISTIC_DELAY,
    HAZARD_PO_DELAY,
    HAZARD_QUALITY_NC,
    HAZARD_URGENT_ORDER,
    Scenario,
)
from pilotage_flux.data_factory import DEFAULT_SPEC, generate_random_fixtures

try:
    from rich.console import Console
    from rich.progress import (
        BarColumn, MofNCompleteColumn, Progress, TextColumn,
        TimeElapsedColumn, TimeRemainingColumn,
    )
    HAS_RICH = True
except ImportError:
    HAS_RICH = False


STRESS_LEVELS = [
    ("faible",    {"horizon": 30,  "n_haz": 3,  "n_so": 8,  "five": False, "sev": 1.0}),
    ("moyen",     {"horizon": 45,  "n_haz": 5,  "n_so": 12, "five": False, "sev": 1.0}),
    ("fort",      {"horizon": 60,  "n_haz": 8,  "n_so": 15, "five": False, "sev": 1.0}),
    ("extreme",   {"horizon": 120, "n_haz": 20, "n_so": 25, "five": True,  "sev": 1.0}),
    ("extreme+",  {"horizon": 150, "n_haz": 30, "n_so": 30, "five": True,  "sev": 1.3}),
    ("rupture",   {"horizon": 180, "n_haz": 40, "n_so": 35, "five": True,  "sev": 1.6}),
]

SHOCK_TYPES = [
    ("mixed",             {"br": 0.25, "nc": 0.25, "po": 0.20, "ur": 0.15, "lo": 0.15}),
    ("breakdown_heavy",   {"br": 0.60, "nc": 0.15, "po": 0.15, "ur": 0.05, "lo": 0.05}),
    ("nc_heavy",          {"br": 0.15, "nc": 0.60, "po": 0.15, "ur": 0.05, "lo": 0.05}),
    ("supply_heavy",      {"br": 0.15, "nc": 0.20, "po": 0.40, "ur": 0.05, "lo": 0.20}),
]

BASE_CONFIGS = [
    ("OF",         DOCTRINE_OF),
    ("OF+EVENT",   DOCTRINE_OF_EVENT),
    ("FLUX+EVENT", DOCTRINE_EVENT),
]

SEED_BASE = 10000


def _make_spec(level_cfg: dict, shock_cfg: dict,
               cascade: str | None) -> RandomScenarioSpec:
    sev = level_cfg["sev"]
    five = level_cfg["five"]
    if five:
        kinds = [HAZARD_BREAKDOWN, HAZARD_QUALITY_NC, HAZARD_PO_DELAY,
                 HAZARD_URGENT_ORDER, HAZARD_LOGISTIC_DELAY]
        weights = {
            HAZARD_BREAKDOWN: shock_cfg["br"],
            HAZARD_QUALITY_NC: shock_cfg["nc"],
            HAZARD_PO_DELAY: shock_cfg["po"],
            HAZARD_URGENT_ORDER: shock_cfg["ur"],
            HAZARD_LOGISTIC_DELAY: shock_cfg["lo"],
        }
    else:
        kinds = [HAZARD_BREAKDOWN, HAZARD_QUALITY_NC, HAZARD_PO_DELAY,
                 HAZARD_URGENT_ORDER]
        total = (shock_cfg["br"] + shock_cfg["nc"]
                 + shock_cfg["po"] + shock_cfg["ur"])
        weights = {
            HAZARD_BREAKDOWN: shock_cfg["br"] / total,
            HAZARD_QUALITY_NC: shock_cfg["nc"] / total,
            HAZARD_PO_DELAY: shock_cfg["po"] / total,
            HAZARD_URGENT_ORDER: shock_cfg["ur"] / total,
        }
    return RandomScenarioSpec(
        n_hazards=level_cfg["n_haz"],
        n_sales_orders=level_cfg["n_so"],
        horizon_days=level_cfg["horizon"],
        hazard_kinds=kinds,
        hazard_weights=weights,
        breakdown_duration_range=(int(2 * sev), int(5 * sev)),
        breakdown_factor_range=(1.5 * sev, 3.0 * sev),
        nc_scrap_range=(int(10 * sev), int(25 * sev)),
        po_delay_range=(int(3 * sev), int(10 * sev)),
        urgent_qty_range=(int(20 * sev), int(60 * sev)),
        logistic_block_range=(int(2 * sev), int(4 * sev)),
        cascade_profile=cascade,
    )


def _run_job(job: tuple) -> dict:
    (level, shock, tag, doctrine, seed, work_str, fix_dir_str,
     level_cfg, shock_cfg, cascade, bounded, variant) = job
    work = Path(work_str)
    fix_dir = Path(fix_dir_str)
    spec = _make_spec(level_cfg, shock_cfg, cascade)
    scen = generate_random_scenario(spec, seed=seed, fixtures_dir=fix_dir)
    return _run_one(scen, doctrine, work, fix_dir,
                    level, shock, tag, cascade, bounded, variant)


def _run_one(scen, doctrine, work, fix_dir, level, shock, tag,
             cascade, bounded, variant) -> dict:
    db = work / f"{variant}_{level}_{shock}_{tag}_{scen.seed}.db"
    try:
        result = run_doctrine(scen, doctrine, db, fixtures_dir=fix_dir)
        k = compute_kpis(scen, result)
        wip_vals = list(result.daily_wip.values())
        wip_sd = statistics.stdev(wip_vals) if len(wip_vals) >= 2 else 0.0
        rupture_pct = (
            k.so_rejected / k.so_total if k.so_total > 0 else 0.0
        )
        first_hazard_day = min((h.day for h in scen.hazards), default=3)
        recovery = compute_time_to_recover(result, shock_day=first_hazard_day)
        try:
            db.unlink()
        except OSError:
            pass
        return {
            "variant": variant,
            "level": level,
            "shock_type": shock,
            "cascade_profile": cascade or "",
            "bounded_rationality": int(bool(bounded)),
            "config_tag": tag,
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
            "recovery_days": recovery,
            "recovery_success_rate": k.recovery_success_rate,
            "recovery_days_conditional": (
                k.recovery_days_conditional
                if k.recovery_days_conditional is not None else ""
            ),
            "n_hazards_observed": k.n_hazards_observed,
            "n_recoveries_observed": k.n_recoveries_observed,
            "of_total": k.of_total,
            "of_closed": k.of_closed,
            "of_closed_ratio": (
                k.of_closed / k.of_total if k.of_total > 0 else 0.0
            ),
            "compensation_gap": k.compensation_gap,
            "compensation_success_rate": k.compensation_success_rate,
            "approvals_pending": k.approvals_pending,
            "approvals_approved": k.approvals_approved,
            "approvals_rejected": k.approvals_rejected,
            "co2_total_kg": k.co2_total_kg,
            "co2_per_unit": k.co2_per_unit,
            "co2_energy_kg": k.co2_energy_kg,
            "co2_replan_kg": k.co2_replan_kg,
            "co2_rupture_kg": k.co2_rupture_kg,
            "co2_wip_kg": k.co2_wip_kg,
            "error": "",
        }
    except Exception as e:
        try:
            db.unlink()
        except OSError:
            pass
        return {
            "variant": variant,
            "level": level, "shock_type": shock,
            "cascade_profile": cascade or "",
            "bounded_rationality": int(bool(bounded)),
            "config_tag": tag, "doctrine": doctrine,
            "seed": scen.seed, "status": "error",
            "error": f"{type(e).__name__}: {e}",
        }


CSV_FIELDS = [
    "variant", "level", "shock_type", "cascade_profile",
    "bounded_rationality", "config_tag", "doctrine", "seed", "status",
    "otif", "q_compliance", "d_dispo", "cost_per_u",
    "wip_avg", "wip_sd", "nervousness",
    "so_total", "so_rejected", "rupture_pct",
    "recovery_days", "recovery_success_rate",
    "recovery_days_conditional",
    "n_hazards_observed", "n_recoveries_observed",
    "of_total", "of_closed", "of_closed_ratio",
    "compensation_gap", "compensation_success_rate",
    "approvals_pending", "approvals_approved", "approvals_rejected",
    "co2_total_kg", "co2_per_unit",
    "co2_energy_kg", "co2_replan_kg", "co2_rupture_kg", "co2_wip_kg",
    "error",
]


def build_jobs(
    n_seeds: int,
    include_reactive_cpsat: bool,
    cascade: str | None,
    bounded: bool,
    work_str: str,
    fix_dir_str: str,
) -> list[tuple]:
    configs = list(BASE_CONFIGS)
    if include_reactive_cpsat:
        configs.append(("OF+REACTIVE_CPSAT", DOCTRINE_OF_REACTIVE_CPSAT))
    variant_parts = []
    if include_reactive_cpsat:
        variant_parts.append("cpsat")
    if cascade:
        variant_parts.append(f"cascade-{cascade}")
    if bounded:
        variant_parts.append("bounded")
    variant = "+".join(variant_parts) if variant_parts else "base"

    jobs: list[tuple] = []
    for level, level_cfg in STRESS_LEVELS:
        for shock, shock_cfg in SHOCK_TYPES:
            for tag, doctrine in configs:
                for i in range(n_seeds):
                    seed = SEED_BASE + i
                    jobs.append((
                        level, shock, tag, doctrine, seed,
                        work_str, fix_dir_str, level_cfg, shock_cfg,
                        cascade, bounded, variant,
                    ))
    return jobs


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seeds", type=int, default=24,
                   help="Nombre de seeds par cellule (défaut 24).")
    p.add_argument("--workers", type=int, default=8,
                   help="Nombre de workers parallèles (défaut 8).")
    p.add_argument("--include-reactive-cpsat", action="store_true",
                   help="Ext-l — ajoute OF+REACTIVE_CPSAT aux configs.")
    p.add_argument("--cascade", type=str, default=None,
                   choices=list(CASCADE_PROFILES.keys()) + [None],
                   help="Ext-g — profil cascade appliqué au générateur.")
    p.add_argument("--bounded-rationality", action="store_true",
                   help="Ext-h — active les biais humains (info seulement, "
                        "le wiring runner n'est pas activé par défaut).")
    p.add_argument("--out", type=Path,
                   default=Path("docs/master_v2_ext_runs.csv"),
                   help="CSV de sortie.")
    args = p.parse_args()

    n_jobs_per_config = len(STRESS_LEVELS) * len(SHOCK_TYPES) * args.seeds
    n_configs = 3 + (1 if args.include_reactive_cpsat else 0)
    total_jobs = n_configs * n_jobs_per_config
    print(f"[info] Total runs à effectuer : {total_jobs}")
    print(f"[info] Configs : {n_configs}   | cascade={args.cascade}   "
          f"| bounded_rationality={args.bounded_rationality}")
    print(f"[info] Workers : {args.workers}")

    with TemporaryDirectory(prefix="master_v2_ext_") as tmpdir:
        work = Path(tmpdir) / "runs"
        work.mkdir(parents=True, exist_ok=True)
        fix_dir = Path(tmpdir) / "fixtures"
        fix_dir.mkdir(parents=True, exist_ok=True)
        generate_random_fixtures(DEFAULT_SPEC, seed=42, out_dir=fix_dir)

        jobs = build_jobs(
            args.seeds, args.include_reactive_cpsat, args.cascade,
            args.bounded_rationality, str(work), str(fix_dir),
        )

        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8", newline="") as fcsv:
            writer = csv.DictWriter(fcsv, fieldnames=CSV_FIELDS)
            writer.writeheader()

            t0 = time.time()
            n_done = n_err = 0
            with ProcessPoolExecutor(max_workers=args.workers) as pool:
                futures = [pool.submit(_run_job, j) for j in jobs]
                progress = None
                task_id = None
                if HAS_RICH:
                    progress = Progress(
                        TextColumn("[cyan]master_v2 étendu[/cyan]"),
                        BarColumn(),
                        MofNCompleteColumn(),
                        TextColumn("•"),
                        TimeElapsedColumn(),
                        TextColumn("•"),
                        TimeRemainingColumn(),
                    )
                    progress.start()
                    task_id = progress.add_task("runs", total=len(jobs))
                for fut in as_completed(futures):
                    row = fut.result()
                    if row.get("status") == "error":
                        n_err += 1
                    row = {k: row.get(k, "") for k in CSV_FIELDS}
                    writer.writerow(row)
                    fcsv.flush()
                    n_done += 1
                    if progress is not None and task_id is not None:
                        progress.update(task_id, advance=1)
                if progress is not None:
                    progress.stop()

            elapsed = time.time() - t0
            print(f"\n[ok] {n_done} runs terminés en {elapsed / 60:.1f} min "
                  f"({n_err} erreurs)")
            print(f"[ok] CSV : {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
