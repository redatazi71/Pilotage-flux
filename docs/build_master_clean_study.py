"""Étude master consolidée — reset from scratch après les 3 fixes doctrinaux.

Contexte : les études précédentes mesuraient une implémentation partielle
(apply_cpm_absorption non wireé, 7 flags smoothing FLUX+EVENT à 0). Les
correctifs successifs ont modifié la sémantique de plusieurs doctrines.
Ce script produit LA campagne de référence propre pour le paper RFGI.

Protocole :
  - 4 niveaux de stress : faible / moyen / fort / extrême
  - 5 configurations : OF / OF+EVENT / FLUX+EVENT / OF+EVENT+BCE / FLUX+EVENT+BCE
  - 10 seeds par (niveau × config)
  - Sequentiel, fixtures identiques entre configs

Total : 200 runs. Sortie :
  - docs/master_clean_study_runs.csv
  - docs/master_clean_study_summary.json
  - docs/master_clean_study_report.md
"""
from __future__ import annotations

import csv
import json
import statistics
import sys
import time
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


CONFIGS = [
    ("OF",              DOCTRINE_OF),
    ("OF+EVENT",        DOCTRINE_OF_EVENT),
    ("FLUX+EVENT",      DOCTRINE_EVENT),
    ("OF+EVENT+BCE",    DOCTRINE_OF_EVENT_BCE),
    ("FLUX+EVENT+BCE",  DOCTRINE_EVENT_BCE),
]

STRESS_LEVELS = [
    ("faible",   {"horizon": 30,  "n_haz": 3,  "n_so": 8,  "five_types": False}),
    ("moyen",    {"horizon": 45,  "n_haz": 5,  "n_so": 12, "five_types": False}),
    ("fort",     {"horizon": 60,  "n_haz": 8,  "n_so": 15, "five_types": False}),
    ("extrême", {"horizon": 120, "n_haz": 20, "n_so": 25, "five_types": True}),
]

N_SEEDS = 10
SEED_BASE = 5000

HERE = Path(__file__).resolve().parent
RUNS_CSV = HERE / "master_clean_study_runs.csv"
SUMMARY_JSON = HERE / "master_clean_study_summary.json"
REPORT_MD = HERE / "master_clean_study_report.md"


def _make_spec(cfg) -> RandomScenarioSpec:
    kinds = [
        HAZARD_BREAKDOWN, HAZARD_QUALITY_NC,
        HAZARD_PO_DELAY, HAZARD_URGENT_ORDER,
    ]
    weights = {
        HAZARD_BREAKDOWN: 0.30, HAZARD_QUALITY_NC: 0.30,
        HAZARD_PO_DELAY: 0.20, HAZARD_URGENT_ORDER: 0.20,
    }
    if cfg["five_types"]:
        kinds.append(HAZARD_LOGISTIC_DELAY)
        weights = {
            HAZARD_BREAKDOWN: 0.28, HAZARD_QUALITY_NC: 0.24,
            HAZARD_PO_DELAY: 0.20, HAZARD_URGENT_ORDER: 0.16,
            HAZARD_LOGISTIC_DELAY: 0.12,
        }
    return RandomScenarioSpec(
        n_hazards=cfg["n_haz"],
        n_sales_orders=cfg["n_so"],
        horizon_days=cfg["horizon"],
        hazard_kinds=kinds,
        hazard_weights=weights,
    )


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
            "recovery_days": recovery_days,
            "of_total": k.of_total,
        }
    except Exception as e:
        return {
            "level": level, "config_tag": tag, "doctrine": doctrine,
            "seed": scen.seed, "status": "crashed", "error": str(e)[:120],
        }


def _agg(runs: list[dict]) -> dict:
    from collections import defaultdict
    by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in runs:
        if r.get("status") == "ok":
            by_key[(r["level"], r["config_tag"])].append(r)

    def stats(rs, key):
        vals = [r.get(key, 0.0) for r in rs]
        if not vals:
            return {"mean": 0.0, "std": 0.0, "n": 0}
        return {
            "mean": statistics.mean(vals),
            "std": statistics.stdev(vals) if len(vals) >= 2 else 0.0,
            "n": len(vals),
        }

    out: dict[str, dict[str, dict]] = {}
    for (level, tag), rs in by_key.items():
        out.setdefault(level, {})[tag] = {
            "n_runs": len(rs),
            "otif": stats(rs, "otif"),
            "q": stats(rs, "q_compliance"),
            "d": stats(rs, "d_dispo"),
            "cost_per_u": stats(rs, "cost_per_u"),
            "wip_avg": stats(rs, "wip_avg"),
            "wip_sd": stats(rs, "wip_sd"),
            "nervousness": stats(rs, "nervousness"),
            "rupture_pct": stats(rs, "rupture_pct"),
            "recovery_days": stats(rs, "recovery_days"),
        }
    return out


def _write_report(agg: dict) -> None:
    lines = [
        "# Étude master consolidée — 5 configs × 4 stress × 10 seeds",
        "",
        "Campagne propre post-fixes doctrinaux :",
        "- Fix 1 : apply_cpm_absorption wireé dans OF+EVENT et FLUX+EVENT",
        "- Fix 2 : 7 flags smoothing activés dans FLUX+EVENT",
        "",
        "Total : 200 runs sequentiels, fixtures identiques entre configs.",
        "",
    ]
    for level, cfg in STRESS_LEVELS:
        a = agg.get(level, {})
        if not a:
            continue
        lines.append(
            f"## Stress {level.upper()} "
            f"({cfg['horizon']}j × {cfg['n_haz']} hazards)"
        )
        lines.append("")
        lines.append(
            "| Config | OTIF | Q | D | €/u | WIP moy | WIP σ | Nervosité | Rupture % | Recovery j |"
        )
        lines.append("|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|")
        for tag, _ in CONFIGS:
            m = a.get(tag)
            if not m:
                continue
            lines.append(
                f"| {tag} | {m['otif']['mean']:.3f} | "
                f"{m['q']['mean']:.3f} | {m['d']['mean']:.3f} | "
                f"{m['cost_per_u']['mean']:.2f} | "
                f"{m['wip_avg']['mean']:.2f} | {m['wip_sd']['mean']:.2f} | "
                f"{m['nervousness']['mean']:.3f} | "
                f"{m['rupture_pct']['mean']:.1%} | "
                f"{m['recovery_days']['mean']:.1f} |"
            )
        lines.append("")
        # Écarts-types (indication de robustesse)
        lines.append(
            "**Écarts-types (σ inter-seeds)** — pour référence :"
        )
        lines.append("")
        lines.append(
            "| Config | σ OTIF | σ €/u | σ Nervosité | σ Rupture |"
        )
        lines.append("|---|:-:|:-:|:-:|:-:|")
        for tag, _ in CONFIGS:
            m = a.get(tag)
            if not m:
                continue
            lines.append(
                f"| {tag} | {m['otif']['std']:.3f} | "
                f"{m['cost_per_u']['std']:.2f} | "
                f"{m['nervousness']['std']:.3f} | "
                f"{m['rupture_pct']['std']:.1%} |"
            )
        lines.append("")

    lines.append("## Différentiels doctrinaux clés")
    lines.append("")
    for level, _ in STRESS_LEVELS:
        a = agg.get(level, {})
        if not a:
            continue
        of = a.get("OF")
        ofe = a.get("OF+EVENT")
        fe = a.get("FLUX+EVENT")
        ofe_bce = a.get("OF+EVENT+BCE")
        fe_bce = a.get("FLUX+EVENT+BCE")
        lines.append(f"### Stress {level}")
        lines.append("")
        if of and ofe:
            lines.append(
                f"- **OF+EVENT vs OF** : ΔOTIF "
                f"{(ofe['otif']['mean'] - of['otif']['mean']):+.3f}, "
                f"Δ€/u {(ofe['cost_per_u']['mean'] - of['cost_per_u']['mean']):+.2f}, "
                f"Δnervosité {(ofe['nervousness']['mean'] - of['nervousness']['mean']):+.3f}"
            )
        if ofe and fe:
            lines.append(
                f"- **FLUX+EVENT vs OF+EVENT** : ΔOTIF "
                f"{(fe['otif']['mean'] - ofe['otif']['mean']):+.3f}, "
                f"Δ€/u {(fe['cost_per_u']['mean'] - ofe['cost_per_u']['mean']):+.2f}, "
                f"ΔWIP σ {(fe['wip_sd']['mean'] - ofe['wip_sd']['mean']):+.2f}, "
                f"Δrupture {(fe['rupture_pct']['mean'] - ofe['rupture_pct']['mean']):+.1%}"
            )
        if ofe and ofe_bce:
            lines.append(
                f"- **OF+EVENT+BCE vs OF+EVENT** : ΔOTIF "
                f"{(ofe_bce['otif']['mean'] - ofe['otif']['mean']):+.3f}, "
                f"Δ€/u {(ofe_bce['cost_per_u']['mean'] - ofe['cost_per_u']['mean']):+.2f}, "
                f"Δnervosité {(ofe_bce['nervousness']['mean'] - ofe['nervousness']['mean']):+.3f}"
            )
        if fe and fe_bce:
            lines.append(
                f"- **FLUX+EVENT+BCE vs FLUX+EVENT** : ΔOTIF "
                f"{(fe_bce['otif']['mean'] - fe['otif']['mean']):+.3f}, "
                f"Δ€/u {(fe_bce['cost_per_u']['mean'] - fe['cost_per_u']['mean']):+.2f}, "
                f"Δnervosité {(fe_bce['nervousness']['mean'] - fe['nervousness']['mean']):+.3f}"
            )
        lines.append("")

    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"OK {REPORT_MD}")


def main() -> int:
    seeds = list(range(SEED_BASE, SEED_BASE + N_SEEDS))
    n_total = len(seeds) * len(CONFIGS) * len(STRESS_LEVELS)
    print(f"=== Master clean study — {len(STRESS_LEVELS)} niveaux × "
          f"{len(CONFIGS)} configs × {len(seeds)} seeds = {n_total} runs ===")
    t0 = time.time()

    all_runs: list[dict] = []
    with TemporaryDirectory(prefix="master_clean_") as tmp:
        work = Path(tmp)
        fix_dir = work / "fix"
        generate_random_fixtures(DEFAULT_SPEC, seed=42, out_dir=fix_dir)

        done = crashed = 0
        for level, cfg in STRESS_LEVELS:
            spec = _make_spec(cfg)
            for seed in seeds:
                scen = generate_random_scenario(spec, seed=seed,
                                                 fixtures_dir=fix_dir)
                for tag, doctrine in CONFIGS:
                    r = _run_one(scen, doctrine, work, fix_dir, tag, level)
                    all_runs.append(r)
                    done += 1
                    if r.get("status") == "crashed":
                        crashed += 1
                    if done % 20 == 0 or done == n_total:
                        elapsed = time.time() - t0
                        eta = (elapsed / done) * (n_total - done)
                        print(f"  ... {done}/{n_total} "
                              f"({crashed} crashs) "
                              f"— {elapsed:.0f}s elapsed, "
                              f"ETA {eta:.0f}s")

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

    total_time = time.time() - t0
    print(f"\n=== Résumé master ({total_time:.0f}s total) ===")
    for level, _ in STRESS_LEVELS:
        a = agg.get(level, {})
        if not a:
            continue
        print(f"\n[stress {level}]")
        print(f"{'Config':<18} {'OTIF':>7} {'€/u':>7} "
              f"{'WIP σ':>7} {'Nerv':>7} {'Rupt%':>7} {'Rec j':>6}")
        for tag, _ in CONFIGS:
            m = a.get(tag)
            if not m:
                continue
            print(f"{tag:<18} {m['otif']['mean']:>7.3f} "
                  f"{m['cost_per_u']['mean']:>7.2f} "
                  f"{m['wip_sd']['mean']:>7.2f} "
                  f"{m['nervousness']['mean']:>7.3f} "
                  f"{m['rupture_pct']['mean']:>7.1%} "
                  f"{m['recovery_days']['mean']:>6.1f}")
    return 0 if crashed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
