"""Comparaison directe paires × {BCE on, BCE off} appariée par seed.

Pour chaque (paire, seed, saturation) :
  - run avec FLUX+EVENT+BCE → KPIs
  - run avec FLUX+EVENT seul (sans BCE) → KPIs
Différence appariée → apport BCE par paire.

Sortie :
  - docs/pair_bce_vs_event_runs.csv (1 ligne par run)
  - docs/pair_bce_vs_event_paired_diff.json (Δ par paire)

Usage :
    python docs/run_pair_bce_vs_event.py [--seeds 20]
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pilotage_flux.comparative.domain_pair_stress import (
    DOMAINS,
    all_pairs,
    pair_stress_scenario,
)
from pilotage_flux.comparative.qcds_kpis import extract_qcds_kpis
from pilotage_flux.comparative.runner import run_doctrine
from pilotage_flux.comparative.saturation import (
    calibrate_scenario_to_saturation,
)
from pilotage_flux.comparative.scenario import (
    DOCTRINE_EVENT,
    DOCTRINE_EVENT_BCE,
)
from pilotage_flux.db import db_session


FIXTURES = Path("data/fixtures_extended")
if not FIXTURES.exists():
    FIXTURES = Path("data/fixtures_v1")


SATURATION = 0.94
PILOTAGES = (DOCTRINE_EVENT_BCE, DOCTRINE_EVENT)
N_SEEDS_DEFAULT = 20


def run_one(d_a: str, d_b: str, seed: int, doctrine: str) -> dict:
    scenario = pair_stress_scenario(d_a, d_b, seed=seed)
    try:
        scenario = calibrate_scenario_to_saturation(
            scenario, SATURATION, fixtures_dir=FIXTURES,
        )
    except Exception as e:
        return {
            "pair": f"{d_a}|{d_b}", "domain_a": d_a, "domain_b": d_b,
            "seed": seed, "doctrine": doctrine,
            "status": "crashed", "error": f"calib: {str(e)[:80]}",
        }
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "p.db"
        try:
            result = run_doctrine(
                scenario, doctrine, db,
                fixtures_dir=FIXTURES,
                evaluate_rejections=False,
            )
            with db_session(db) as conn:
                kpis = extract_qcds_kpis(conn, result, scenario)
        except Exception as e:
            return {
                "pair": f"{d_a}|{d_b}", "domain_a": d_a,
                "domain_b": d_b, "seed": seed,
                "doctrine": doctrine,
                "status": "crashed", "error": str(e)[:80],
            }
    return {
        "pair": f"{d_a}|{d_b}",
        "domain_a": d_a, "domain_b": d_b,
        "seed": seed, "doctrine": doctrine,
        "status": "ok",
        "otif": kpis.otif, "yield_pct": kpis.yield_pct,
        "wip_mean": kpis.wip_mean, "wip_p95": kpis.wip_p95,
        "lateness_mean_days": kpis.lateness_mean_days,
        "mean_recovery_days": kpis.mean_recovery_days or 0.0,
        "n_recoveries": kpis.n_recoveries_observed,
        "n_hazards_observed": kpis.n_hazards_observed,
    }


def paired_diff(runs: list[dict]) -> list[dict]:
    """Pour chaque (paire, seed) : diff BCE - non-BCE par KPI.

    Renvoie par paire la moyenne sur seeds des différences.
    """
    # Index runs par (pair, seed, doctrine)
    idx: dict[tuple, dict] = {}
    for r in runs:
        if r.get("status") != "ok":
            continue
        idx[(r["pair"], int(r["seed"]), r["doctrine"])] = r

    kpis = ("otif", "yield_pct", "wip_mean",
             "lateness_mean_days", "mean_recovery_days")

    by_pair: dict[str, dict[str, list[float]]] = {}
    for (pair, seed, doc), r in idx.items():
        if doc != DOCTRINE_EVENT_BCE:
            continue
        non_bce = idx.get((pair, seed, DOCTRINE_EVENT))
        if non_bce is None:
            continue
        d = by_pair.setdefault(
            pair, {k: [] for k in kpis},
        )
        for k in kpis:
            d[k].append(float(r[k]) - float(non_bce[k]))

    out = []
    for pair, diffs in by_pair.items():
        d_a, d_b = pair.split("|")
        entry = {
            "pair": pair, "domain_a": d_a, "domain_b": d_b,
            "n_pairs_compared": len(diffs["otif"]),
        }
        for k in kpis:
            vals = diffs[k]
            if not vals:
                entry[f"delta_{k}_mean"] = 0.0
                entry[f"delta_{k}_sd"] = 0.0
                continue
            entry[f"delta_{k}_mean"] = statistics.mean(vals)
            entry[f"delta_{k}_sd"] = (
                statistics.stdev(vals) if len(vals) >= 2 else 0.0
            )
            entry[f"delta_{k}_median"] = statistics.median(vals)
        # Score combiné Δ : -ΔOTIF (haut = mieux pour non-BCE)
        # ou +Δrecovery (bas = mieux pour BCE)
        # Convention : positif = BCE meilleur sur l'apport
        entry["bce_advantage_otif"] = entry["delta_otif_mean"]
        entry["bce_advantage_recovery"] = -entry["delta_mean_recovery_days_mean"]
        out.append(entry)
    # Tri par bce_advantage_recovery (paires où BCE gagne le plus en
    # récupération)
    out.sort(key=lambda x: x["bce_advantage_recovery"], reverse=True)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=N_SEEDS_DEFAULT)
    args = parser.parse_args()
    seeds = list(range(42, 42 + args.seeds))
    pairs = all_pairs()
    n_total = len(pairs) * len(seeds) * len(PILOTAGES)
    print(
        f"Comparaison BCE vs non-BCE : {len(pairs)} paires × "
        f"{len(seeds)} seeds × {len(PILOTAGES)} pilotages = "
        f"{n_total} runs @ saturation {SATURATION}"
    )

    runs = []
    done = crashed = 0
    for d_a, d_b in pairs:
        for seed in seeds:
            for doctrine in PILOTAGES:
                r = run_one(d_a, d_b, seed, doctrine)
                runs.append(r)
                done += 1
                if r.get("status") == "crashed":
                    crashed += 1
                if done % 50 == 0:
                    print(f"  ... {done}/{n_total} ({crashed} crashs)")

    csv_path = Path("docs/pair_bce_vs_event_runs.csv")
    all_fields = sorted({k for r in runs for k in r.keys()})
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=all_fields)
        w.writeheader()
        for r in runs:
            w.writerow(r)
    print(f"\nCSV : {csv_path}")

    diffs = paired_diff(runs)
    diff_path = Path("docs/pair_bce_vs_event_paired_diff.json")
    diff_path.write_text(json.dumps(diffs, indent=2, default=str))
    print(f"Diffs appariés : {diff_path}\n")

    print("=== Top 10 paires où BCE apporte le PLUS sur la "
          "récupération (Δrecovery_days, neg = BCE meilleur) ===\n")
    print(f"{'Rang':<5} {'Paire':<35} "
          f"{'Δrecov':>9} {'ΔOTIF':>8} {'n':>4}")
    for i, e in enumerate(diffs[:10], start=1):
        print(
            f"{i:<5} {e['pair']:<35} "
            f"{e['delta_mean_recovery_days_mean']:>+9.3f} "
            f"{e['delta_otif_mean']:>+8.4f} "
            f"{e['n_pairs_compared']:>4}"
        )

    print("\n=== Top 10 paires où BCE est le MOINS bon "
          "(BCE worse on recovery) ===\n")
    diffs_worst = sorted(
        diffs,
        key=lambda x: x["delta_mean_recovery_days_mean"],
        reverse=True,
    )
    for i, e in enumerate(diffs_worst[:10], start=1):
        print(
            f"{i:<5} {e['pair']:<35} "
            f"{e['delta_mean_recovery_days_mean']:>+9.3f} "
            f"{e['delta_otif_mean']:>+8.4f} "
            f"{e['n_pairs_compared']:>4}"
        )

    print(f"\n=== {done - crashed} ok / {crashed} crashs sur "
          f"{n_total} ===")
    return 0 if crashed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
