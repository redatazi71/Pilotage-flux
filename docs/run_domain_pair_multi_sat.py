"""Étude paires domaines × saturations multiples × N seeds étendu.

Configurable : saturations à balayer + N seeds.

Sortie par saturation :
  - docs/domain_pair_sat{XX}_runs.csv
  - docs/domain_pair_sat{XX}_ranking.json

Comparaison transverse :
  - docs/domain_pair_multi_sat_comparison.json (top 10 par sat
    + analyse stabilité du ranking)

Usage :
    python docs/run_domain_pair_multi_sat.py [--seeds 20]
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
from pilotage_flux.comparative.scenario import DOCTRINE_EVENT_BCE
from pilotage_flux.db import db_session


FIXTURES = Path("data/fixtures_extended")
if not FIXTURES.exists():
    FIXTURES = Path("data/fixtures_v1")


PILOTAGE = DOCTRINE_EVENT_BCE
SATURATIONS = (0.94, 0.96)
N_SEEDS_DEFAULT = 20


def run_one(d_a: str, d_b: str, seed: int, saturation: float) -> dict:
    scenario = pair_stress_scenario(d_a, d_b, seed=seed)
    try:
        scenario = calibrate_scenario_to_saturation(
            scenario, saturation, fixtures_dir=FIXTURES,
        )
    except Exception as e:
        return {
            "pair": f"{d_a}|{d_b}", "domain_a": d_a, "domain_b": d_b,
            "seed": seed, "saturation": saturation,
            "status": "crashed", "error": f"calib: {str(e)[:80]}",
        }
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "p.db"
        try:
            result = run_doctrine(
                scenario, PILOTAGE, db,
                fixtures_dir=FIXTURES,
                evaluate_rejections=False,
            )
            with db_session(db) as conn:
                kpis = extract_qcds_kpis(conn, result, scenario)
        except Exception as e:
            return {
                "pair": f"{d_a}|{d_b}", "domain_a": d_a,
                "domain_b": d_b, "seed": seed,
                "saturation": saturation,
                "status": "crashed", "error": str(e)[:80],
            }
    return {
        "pair": f"{d_a}|{d_b}",
        "domain_a": d_a, "domain_b": d_b,
        "seed": seed, "saturation": saturation, "status": "ok",
        "otif": kpis.otif, "yield_pct": kpis.yield_pct,
        "wip_mean": kpis.wip_mean, "wip_p95": kpis.wip_p95,
        "lateness_mean_days": kpis.lateness_mean_days,
        "mean_recovery_days": kpis.mean_recovery_days or 0.0,
        "n_recoveries": kpis.n_recoveries_observed,
        "n_hazards_observed": kpis.n_hazards_observed,
    }


def aggregate(runs: list[dict]) -> list[dict]:
    from collections import defaultdict
    by_pair: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        if r.get("status") == "ok":
            by_pair[r["pair"]].append(r)
    out = []
    for pair, rs in by_pair.items():
        d_a, d_b = pair.split("|")
        otifs = [r["otif"] for r in rs]
        recoveries = [r["mean_recovery_days"] for r in rs]
        yields_ = [r["yield_pct"] for r in rs]
        wips = [r["wip_mean"] for r in rs]
        otif_mean = statistics.mean(otifs)
        recovery_mean = statistics.mean(recoveries)
        combined = (1 - otif_mean) + recovery_mean / 30.0
        out.append({
            "pair": pair, "domain_a": d_a, "domain_b": d_b,
            "n_seeds": len(rs),
            "otif_mean": otif_mean,
            "otif_sd": (
                statistics.stdev(otifs) if len(otifs) >= 2 else 0.0
            ),
            "recovery_mean_days": recovery_mean,
            "recovery_sd": (
                statistics.stdev(recoveries)
                if len(recoveries) >= 2 else 0.0
            ),
            "yield_mean": statistics.mean(yields_),
            "wip_mean": statistics.mean(wips),
            "combined_impact_score": combined,
        })
    out.sort(key=lambda x: x["combined_impact_score"], reverse=True)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=N_SEEDS_DEFAULT)
    args = parser.parse_args()
    seeds = list(range(42, 42 + args.seeds))
    pairs = all_pairs()
    print(
        f"Étude multi-sat : {len(pairs)} paires × {len(seeds)} seeds "
        f"× {len(SATURATIONS)} saturations = "
        f"{len(pairs) * len(seeds) * len(SATURATIONS)} runs total"
    )

    rankings_by_sat: dict[float, list[dict]] = {}

    for sat in SATURATIONS:
        sat_pct = int(round(sat * 100))
        runs = []
        n_done = n_crash = 0
        n_total = len(pairs) * len(seeds)
        print(f"\n=== Saturation {sat:.2f} ===")
        for d_a, d_b in pairs:
            for seed in seeds:
                r = run_one(d_a, d_b, seed, sat)
                runs.append(r)
                n_done += 1
                if r.get("status") == "crashed":
                    n_crash += 1
                if n_done % 50 == 0:
                    print(
                        f"  ... {n_done}/{n_total} ({n_crash} crashs)"
                    )
        csv_path = Path(f"docs/domain_pair_sat{sat_pct}_runs.csv")
        all_fields = sorted({k for r in runs for k in r.keys()})
        with csv_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=all_fields)
            w.writeheader()
            for r in runs:
                w.writerow(r)
        agg = aggregate(runs)
        rank_path = Path(
            f"docs/domain_pair_sat{sat_pct}_ranking.json"
        )
        rank_path.write_text(json.dumps(agg, indent=2, default=str))
        rankings_by_sat[sat] = agg
        print(f"  CSV : {csv_path}  Ranking : {rank_path}")
        print(f"  Top 5 :")
        for i, e in enumerate(agg[:5], start=1):
            print(
                f"    {i}. {e['pair']:<35} OTIF {e['otif_mean']:.1%} "
                f"Recov {e['recovery_mean_days']:.2f}j "
                f"Score {e['combined_impact_score']:.4f}"
            )

    # Comparaison transverse : top 10 par saturation + analyse de
    # stabilité du ranking
    comparison: dict = {
        "saturations": list(SATURATIONS),
        "n_seeds": args.seeds,
        "top_10_per_saturation": {},
        "rank_stability": {},
    }
    for sat, agg in rankings_by_sat.items():
        top10 = [
            {"rank": i + 1, **e} for i, e in enumerate(agg[:10])
        ]
        comparison["top_10_per_saturation"][f"{sat:.2f}"] = top10
    # Stabilité : pour chaque paire, son rang à chaque saturation
    for d_a, d_b in pairs:
        key = f"{d_a}|{d_b}"
        ranks = {}
        for sat, agg in rankings_by_sat.items():
            for i, e in enumerate(agg):
                if e["pair"] == key:
                    ranks[f"{sat:.2f}"] = i + 1
                    break
        comparison["rank_stability"][key] = ranks

    cmp_path = Path("docs/domain_pair_multi_sat_comparison.json")
    cmp_path.write_text(json.dumps(comparison, indent=2, default=str))
    print(f"\nComparaison multi-sat : {cmp_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
