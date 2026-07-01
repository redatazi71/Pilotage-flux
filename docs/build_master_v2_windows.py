"""Master v2 study — Windows-ready avec visualisation live.

Protocole :
  6 niveaux de stress × 4 types de choc dominant × 5 configs × N seeds
  Test smoke : --seeds 5 → 600 runs (~10-15 min sur PC standard)
  Campagne complète : --seeds 60 → 7200 runs (~2h30 selon CPU)

Fonctionnalités :
  - Séquentiel, déterministe (reproductible)
  - Sauvegarde incrémentale CSV (résilient à Ctrl+C)
  - Reprise depuis un run interrompu (--resume)
  - Visualisation live : progress bar + tableau KPIs par config (rich)
  - Fichier status JSON pour monitoring externe
  - Rapport agrégé automatique en fin de run

Usage Windows :
  python docs\\build_master_v2_windows.py --seeds 5      # test 600
  python docs\\build_master_v2_windows.py --seeds 60     # complet 7200
  python docs\\build_master_v2_windows.py --resume       # reprise
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
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


try:
    from rich.console import Console
    from rich.live import Live
    from rich.table import Table
    from rich.layout import Layout
    from rich.panel import Panel
    from rich.progress import (
        Progress, BarColumn, TextColumn, TimeRemainingColumn,
        MofNCompleteColumn, TimeElapsedColumn,
    )
    HAS_RICH = True
except ImportError:
    HAS_RICH = False
    print("[WARN] rich non installé — fallback texte. "
          "pip install rich pour la visualisation live.")


# ---------- Configuration protocole ------------------------------------

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

CONFIGS = [
    ("OF",              DOCTRINE_OF),
    ("OF+EVENT",        DOCTRINE_OF_EVENT),
    ("FLUX+EVENT",      DOCTRINE_EVENT),
    ("OF+EVENT+BCE",    DOCTRINE_OF_EVENT_BCE),
    ("FLUX+EVENT+BCE",  DOCTRINE_EVENT_BCE),
]

SEED_BASE = 10000


HERE = Path(__file__).resolve().parent
RUNS_CSV = HERE / "master_v2_runs.csv"
SUMMARY_JSON = HERE / "master_v2_summary.json"
REPORT_MD = HERE / "master_v2_report.md"
STATUS_JSON = HERE / "master_v2_status.json"


# ---------- Génération scenario adapté au niveau × type -----------------

def _make_spec(level_cfg: dict, shock_cfg: dict) -> RandomScenarioSpec:
    sev = level_cfg["sev"]
    five = level_cfg["five"]

    if five:
        kinds = [
            HAZARD_BREAKDOWN, HAZARD_QUALITY_NC, HAZARD_PO_DELAY,
            HAZARD_URGENT_ORDER, HAZARD_LOGISTIC_DELAY,
        ]
        weights = {
            HAZARD_BREAKDOWN: shock_cfg["br"],
            HAZARD_QUALITY_NC: shock_cfg["nc"],
            HAZARD_PO_DELAY: shock_cfg["po"],
            HAZARD_URGENT_ORDER: shock_cfg["ur"],
            HAZARD_LOGISTIC_DELAY: shock_cfg["lo"],
        }
    else:
        kinds = [
            HAZARD_BREAKDOWN, HAZARD_QUALITY_NC, HAZARD_PO_DELAY,
            HAZARD_URGENT_ORDER,
        ]
        # Renormalise en excluant logistic
        total = shock_cfg["br"] + shock_cfg["nc"] + shock_cfg["po"] + shock_cfg["ur"]
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
    )


# ---------- Un run --------------------------------------------------------

def _run_job(job: tuple) -> dict:
    """Worker function pour ProcessPoolExecutor.

    Reçoit un tuple pickle-able, régénère la scenario, exécute run_doctrine.
    Chaque worker a son propre process → import répété acceptable.
    """
    (level, shock, tag, doctrine, seed, work_str, fix_dir_str,
     level_cfg, shock_cfg) = job
    work = Path(work_str)
    fix_dir = Path(fix_dir_str)
    spec = _make_spec(level_cfg, shock_cfg)
    scen = generate_random_scenario(spec, seed=seed, fixtures_dir=fix_dir)
    return _run_one(scen, doctrine, work, fix_dir, level, shock, tag)


def _run_one(scen, doctrine, work, fix_dir, level, shock, tag) -> dict:
    db = work / f"{level}_{shock}_{tag}_{scen.seed}.db"
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
        # Nettoyage DB pour éviter la saturation disque sur 7200 runs
        try:
            db.unlink()
        except OSError:
            pass
        return {
            "level": level,
            "shock_type": shock,
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
            "of_total": k.of_total,
            "of_closed": k.of_closed,
            "of_closed_ratio": (
                k.of_closed / k.of_total if k.of_total > 0 else 0.0
            ),
        }
    except Exception as e:
        return {
            "level": level, "shock_type": shock, "config_tag": tag,
            "doctrine": doctrine, "seed": scen.seed,
            "status": "crashed", "error": str(e)[:200],
        }


# ---------- Visualisation live rich --------------------------------------

class LiveDisplay:
    """Affichage rich avec progress + tableau KPI live."""

    def __init__(self, n_total: int):
        self.n_total = n_total
        self.done = 0
        self.crashed = 0
        self.t0 = time.time()
        self.current = {"level": "", "shock": "", "seed": 0}
        # Running means par config
        self.running: dict[str, list[float]] = {tag: [] for tag, _ in CONFIGS}
        self.running_cost: dict[str, list[float]] = {tag: [] for tag, _ in CONFIGS}
        self.running_nerv: dict[str, list[float]] = {tag: [] for tag, _ in CONFIGS}
        self.running_wip: dict[str, list[float]] = {tag: [] for tag, _ in CONFIGS}
        self.running_rup: dict[str, list[float]] = {tag: [] for tag, _ in CONFIGS}
        self.console = Console() if HAS_RICH else None

    def register_run(self, r: dict) -> None:
        self.done += 1
        if r.get("status") == "crashed":
            self.crashed += 1
            return
        tag = r["config_tag"]
        self.running[tag].append(r["otif"])
        self.running_cost[tag].append(r["cost_per_u"])
        self.running_nerv[tag].append(r["nervousness"])
        self.running_wip[tag].append(r["wip_sd"])
        self.running_rup[tag].append(r["rupture_pct"])
        self.current = {
            "level": r["level"], "shock": r["shock_type"],
            "seed": r["seed"],
        }

    def _mk_table(self) -> Table:
        table = Table(title="KPIs cumulés (moyenne inter-cellules déjà traitées)")
        table.add_column("Config", style="cyan", no_wrap=True)
        table.add_column("N runs", justify="right")
        table.add_column("OTIF", justify="right")
        table.add_column("€/u", justify="right")
        table.add_column("WIP σ", justify="right")
        table.add_column("Nervosité", justify="right")
        table.add_column("Rupture %", justify="right")
        for tag, _ in CONFIGS:
            n = len(self.running[tag])
            if n == 0:
                table.add_row(tag, "0", "—", "—", "—", "—", "—")
                continue
            table.add_row(
                tag, str(n),
                f"{statistics.mean(self.running[tag]):.3f}",
                f"{statistics.mean(self.running_cost[tag]):.2f}",
                f"{statistics.mean(self.running_wip[tag]):.2f}",
                f"{statistics.mean(self.running_nerv[tag]):.3f}",
                f"{statistics.mean(self.running_rup[tag]):.1%}",
            )
        return table

    def _mk_header(self) -> Panel:
        elapsed = time.time() - self.t0
        pct = self.done / self.n_total if self.n_total > 0 else 0
        eta = (elapsed / self.done) * (self.n_total - self.done) if self.done > 0 else 0
        text = (
            f"Progrès : {self.done}/{self.n_total} "
            f"({pct:.1%}) — {self.crashed} crashs\n"
            f"Elapsed : {elapsed:.0f}s   ETA : {eta:.0f}s\n"
            f"En cours : niveau [{self.current['level']}] × "
            f"shock [{self.current['shock']}] × "
            f"seed [{self.current['seed']}]"
        )
        return Panel(text, title="Master v2 — Étude 7200 runs", border_style="green")

    def make_layout(self) -> Layout:
        layout = Layout()
        layout.split(
            Layout(self._mk_header(), size=6, name="header"),
            Layout(self._mk_table(), name="kpis"),
        )
        return layout

    def dump_status(self) -> None:
        elapsed = time.time() - self.t0
        pct = self.done / self.n_total if self.n_total > 0 else 0
        eta = (elapsed / self.done) * (self.n_total - self.done) if self.done > 0 else 0
        stats = {}
        for tag, _ in CONFIGS:
            if not self.running[tag]:
                continue
            stats[tag] = {
                "n": len(self.running[tag]),
                "otif_mean": statistics.mean(self.running[tag]),
                "cost_per_u_mean": statistics.mean(self.running_cost[tag]),
                "wip_sd_mean": statistics.mean(self.running_wip[tag]),
                "nervousness_mean": statistics.mean(self.running_nerv[tag]),
                "rupture_pct_mean": statistics.mean(self.running_rup[tag]),
            }
        STATUS_JSON.write_text(json.dumps({
            "done": self.done,
            "total": self.n_total,
            "crashed": self.crashed,
            "progress_pct": pct,
            "elapsed_seconds": elapsed,
            "eta_seconds": eta,
            "current": self.current,
            "stats_by_config": stats,
        }, indent=2, default=str), encoding="utf-8")


# ---------- Reprise -------------------------------------------------------

def _load_done_keys() -> set[tuple]:
    """Charge les (level, shock, config, seed) déjà présents dans le CSV."""
    if not RUNS_CSV.exists():
        return set()
    done = set()
    with RUNS_CSV.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("status") == "ok":
                done.add((row["level"], row["shock_type"],
                          row["config_tag"], int(row["seed"])))
    return done


CSV_FIELDS = [
    "level", "shock_type", "config_tag", "doctrine", "seed", "status",
    "otif", "q_compliance", "d_dispo", "cost_per_u",
    "wip_avg", "wip_sd", "nervousness",
    "so_total", "so_rejected", "rupture_pct", "recovery_days",
    "of_total", "of_closed", "of_closed_ratio", "error",
]


def _append_row(writer, r: dict, f_handle) -> None:
    row = {k: r.get(k, "") for k in CSV_FIELDS}
    writer.writerow(row)
    f_handle.flush()


# ---------- Agrégation + rapport -----------------------------------------

def _stats(vals: list[float]) -> dict:
    if not vals:
        return {"mean": 0.0, "std": 0.0, "n": 0}
    return {
        "mean": statistics.mean(vals),
        "std": statistics.stdev(vals) if len(vals) >= 2 else 0.0,
        "n": len(vals),
    }


def _aggregate(runs: list[dict]) -> dict:
    from collections import defaultdict
    by_key: dict[tuple, list[dict]] = defaultdict(list)
    for r in runs:
        if r.get("status") == "ok":
            by_key[(r["level"], r["shock_type"], r["config_tag"])].append(r)

    out: dict = {}
    for (level, shock, tag), rs in by_key.items():
        out.setdefault(level, {}).setdefault(shock, {})[tag] = {
            "n_runs": len(rs),
            "otif": _stats([r["otif"] for r in rs]),
            "q": _stats([r["q_compliance"] for r in rs]),
            "d": _stats([r["d_dispo"] for r in rs]),
            "cost_per_u": _stats([r["cost_per_u"] for r in rs]),
            "wip_avg": _stats([r["wip_avg"] for r in rs]),
            "wip_sd": _stats([r["wip_sd"] for r in rs]),
            "nervousness": _stats([r["nervousness"] for r in rs]),
            "rupture_pct": _stats([r["rupture_pct"] for r in rs]),
            "recovery_days": _stats([r["recovery_days"] for r in rs]),
        }
    return out


def _write_report(agg: dict, n_seeds: int) -> None:
    lines = [
        "# Master v2 study — Rapport consolidé",
        "",
        f"Protocole : 6 niveaux stress × 4 types choc × 5 configs × "
        f"{n_seeds} seeds = {6 * 4 * 5 * n_seeds} runs",
        "",
    ]
    for level, level_cfg in STRESS_LEVELS:
        if level not in agg:
            continue
        lines.append(f"## Niveau {level.upper()} "
                     f"({level_cfg['horizon']}j × {level_cfg['n_haz']} hazards, "
                     f"severity ×{level_cfg['sev']})")
        lines.append("")
        for shock, _ in SHOCK_TYPES:
            if shock not in agg[level]:
                continue
            lines.append(f"### Type de choc : {shock}")
            lines.append("")
            lines.append(
                "| Config | N | OTIF | €/u | WIP σ | Nervosité | Rupture % | Recovery j |"
            )
            lines.append("|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|")
            for tag, _ in CONFIGS:
                m = agg[level][shock].get(tag)
                if not m:
                    continue
                lines.append(
                    f"| {tag} | {m['n_runs']} | "
                    f"{m['otif']['mean']:.3f}±{m['otif']['std']:.3f} | "
                    f"{m['cost_per_u']['mean']:.2f}±{m['cost_per_u']['std']:.2f} | "
                    f"{m['wip_sd']['mean']:.2f}±{m['wip_sd']['std']:.2f} | "
                    f"{m['nervousness']['mean']:.3f}±{m['nervousness']['std']:.3f} | "
                    f"{m['rupture_pct']['mean']:.1%}±{m['rupture_pct']['std']:.1%} | "
                    f"{m['recovery_days']['mean']:.1f}±{m['recovery_days']['std']:.1f} |"
                )
            lines.append("")

    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"OK {REPORT_MD}", flush=True)


# ---------- Main ----------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=60,
                        help="Nombre de seeds par cellule "
                             "(défaut 60 = 7200 runs)")
    parser.add_argument("--resume", action="store_true",
                        help="Reprend après interruption "
                             "(saute les cellules déjà présentes dans CSV)")
    parser.add_argument("--no-live", action="store_true",
                        help="Désactive affichage rich (mode texte plain)")
    parser.add_argument("--workers", type=int, default=1,
                        help="Nombre de processes parallèles "
                             "(défaut 1 = séquentiel). Recommandation : "
                             "os.cpu_count() - 1")
    args = parser.parse_args()

    if args.workers < 1:
        args.workers = 1
    if args.workers > 1:
        max_workers = os.cpu_count() or 1
        if args.workers > max_workers:
            print(f"[WARN] workers={args.workers} > cpu_count={max_workers}, "
                  f"clamp à {max_workers}", flush=True)
            args.workers = max_workers

    n_seeds = args.seeds
    seeds = list(range(SEED_BASE, SEED_BASE + n_seeds))
    n_total = len(seeds) * len(CONFIGS) * len(SHOCK_TYPES) * len(STRESS_LEVELS)

    print(f"=== Master v2 — {len(STRESS_LEVELS)} niveaux × "
          f"{len(SHOCK_TYPES)} types × {len(CONFIGS)} configs × "
          f"{n_seeds} seeds = {n_total} runs ===", flush=True)

    done_keys = _load_done_keys() if args.resume else set()
    if done_keys:
        print(f"[RESUME] {len(done_keys)} runs déjà présents — reprise.",
              flush=True)

    all_runs: list[dict] = []
    if args.resume and RUNS_CSV.exists():
        with RUNS_CSV.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                # Reconvert numeric fields for aggregation
                for kf in ["otif", "q_compliance", "d_dispo", "cost_per_u",
                           "wip_avg", "wip_sd", "nervousness", "rupture_pct",
                           "recovery_days", "of_closed_ratio"]:
                    try:
                        row[kf] = float(row[kf])
                    except (ValueError, KeyError):
                        row[kf] = 0.0
                for kf in ["so_total", "so_rejected", "of_total",
                           "of_closed", "seed"]:
                    try:
                        row[kf] = int(row[kf])
                    except (ValueError, KeyError):
                        row[kf] = 0
                all_runs.append(row)

    # Ouvre CSV en append si resume, sinon write
    write_mode = "a" if (args.resume and RUNS_CSV.exists()) else "w"
    csv_file = RUNS_CSV.open(write_mode, encoding="utf-8", newline="")
    writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
    if write_mode == "w":
        writer.writeheader()

    display = LiveDisplay(n_total)
    display.done = len(done_keys)

    # Handler Ctrl+C : sauvegarde propre
    interrupted = {"flag": False}

    def _sig_handler(sig, frame):
        interrupted["flag"] = True
        print("\n[INT] Ctrl+C reçu — sauvegarde et sortie propre...",
              flush=True)

    signal.signal(signal.SIGINT, _sig_handler)

    def _run_all_sequential(live_display=None):
        with TemporaryDirectory(prefix="master_v2_") as tmp:
            work = Path(tmp)
            fix_dir = work / "fix"
            generate_random_fixtures(DEFAULT_SPEC, seed=42, out_dir=fix_dir)

            for level, level_cfg in STRESS_LEVELS:
                for shock_name, shock_cfg in SHOCK_TYPES:
                    spec = _make_spec(level_cfg, shock_cfg)
                    for seed in seeds:
                        scen = generate_random_scenario(
                            spec, seed=seed, fixtures_dir=fix_dir,
                        )
                        for tag, doctrine in CONFIGS:
                            key = (level, shock_name, tag, seed)
                            if key in done_keys:
                                continue
                            r = _run_one(
                                scen, doctrine, work, fix_dir,
                                level, shock_name, tag,
                            )
                            all_runs.append(r)
                            _append_row(writer, r, csv_file)
                            display.register_run(r)
                            if display.done % 20 == 0:
                                display.dump_status()
                            if live_display is not None:
                                live_display.update(display.make_layout())
                            if interrupted["flag"]:
                                return

    def _run_all_parallel(live_display=None):
        with TemporaryDirectory(prefix="master_v2_") as tmp:
            work = Path(tmp)
            fix_dir = work / "fix"
            generate_random_fixtures(DEFAULT_SPEC, seed=42, out_dir=fix_dir)

            # Prépare la liste de jobs pickle-ables
            jobs: list[tuple] = []
            for level, level_cfg in STRESS_LEVELS:
                for shock_name, shock_cfg in SHOCK_TYPES:
                    for seed in seeds:
                        for tag, doctrine in CONFIGS:
                            key = (level, shock_name, tag, seed)
                            if key in done_keys:
                                continue
                            jobs.append((
                                level, shock_name, tag, doctrine, seed,
                                str(work), str(fix_dir),
                                dict(level_cfg), dict(shock_cfg),
                            ))

            print(f"[PARALLEL] {len(jobs)} jobs sur "
                  f"{args.workers} workers", flush=True)

            with ProcessPoolExecutor(max_workers=args.workers) as executor:
                futures = {
                    executor.submit(_run_job, job): job for job in jobs
                }
                for future in as_completed(futures):
                    if interrupted["flag"]:
                        executor.shutdown(wait=False, cancel_futures=True)
                        return
                    try:
                        r = future.result()
                    except Exception as e:
                        job = futures[future]
                        r = {
                            "level": job[0], "shock_type": job[1],
                            "config_tag": job[2], "doctrine": job[3],
                            "seed": job[4], "status": "crashed",
                            "error": str(e)[:200],
                        }
                    all_runs.append(r)
                    _append_row(writer, r, csv_file)
                    display.register_run(r)
                    if display.done % 20 == 0:
                        display.dump_status()
                    if live_display is not None:
                        live_display.update(display.make_layout())

    def _run_all(live_display=None):
        if args.workers > 1:
            _run_all_parallel(live_display)
        else:
            _run_all_sequential(live_display)

    try:
        if HAS_RICH and not args.no_live:
            with Live(display.make_layout(), refresh_per_second=2) as live:
                _run_all(live)
        else:
            _run_all(None)
    finally:
        csv_file.close()
        display.dump_status()

    if interrupted["flag"]:
        print(f"[INT] {display.done}/{n_total} runs sauvegardés. "
              f"Utilise --resume pour continuer.", flush=True)
        return 130

    # Agrégation + rapport
    agg = _aggregate(all_runs)
    SUMMARY_JSON.write_text(
        json.dumps(agg, indent=2, default=str), encoding="utf-8",
    )
    print(f"OK {SUMMARY_JSON}", flush=True)
    _write_report(agg, n_seeds)

    total_time = time.time() - display.t0
    print(f"\n=== TERMINÉ en {total_time:.0f}s "
          f"({display.crashed} crashs sur {display.done} runs) ===",
          flush=True)
    return 0 if display.crashed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
