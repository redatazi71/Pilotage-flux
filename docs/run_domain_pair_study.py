"""Étude 5×5 paires de domaines MACRS — rupture + récupération.

Mesure pour chaque paire (D_a, D_b) ∈ DOMAINS² l'impact sur :
  - **Rupture** : OTIF moyen (plus c'est bas, plus la paire est
                  destructrice du service)
  - **Récupération** : temps moyen de retour du WIP à la bande
                       (plus c'est long, plus la paire est
                       difficile à absorber)
  - **Combiné**     : score normalisé (1 - OTIF) + recovery/30

Lancée sur le pilotage de référence (FLUX+EVENT+BCE = pilotage
le plus complet de la couche cybernétique) à saturation 0.94.

Usage :
    python docs/run_domain_pair_study.py [--seeds N]
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

from pilotage_flux.comparative.bce_kpis_advanced import compute_agilite
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
SATURATION = 0.94      # zone de stress proche rupture
N_SEEDS_DEFAULT = 5


def run_one_pair(d_a: str, d_b: str, seed: int) -> dict:
    """Lance un run sur la paire et collecte rupture + récupération."""
    scenario = pair_stress_scenario(d_a, d_b, seed=seed)
    try:
        scenario = calibrate_scenario_to_saturation(
            scenario, SATURATION, fixtures_dir=FIXTURES,
        )
    except Exception as e:
        return {
            "pair": f"{d_a}|{d_b}",
            "domain_a": d_a, "domain_b": d_b, "seed": seed,
            "status": "crashed",
            "error": f"calibration: {str(e)[:120]}",
        }
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "pair.db"
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
                "pair": f"{d_a}|{d_b}",
                "domain_a": d_a, "domain_b": d_b, "seed": seed,
                "status": "crashed", "error": str(e)[:120],
            }
    return {
        "pair": f"{d_a}|{d_b}",
        "domain_a": d_a, "domain_b": d_b, "seed": seed,
        "status": "ok",
        "otif": kpis.otif,
        "yield_pct": kpis.yield_pct,
        "wip_mean": kpis.wip_mean,
        "wip_p95": kpis.wip_p95,
        "lateness_mean_days": kpis.lateness_mean_days,
        "n_so_late": kpis.n_so_late,
        "mean_recovery_days": kpis.mean_recovery_days or 0.0,
        "n_recoveries": kpis.n_recoveries_observed,
        "n_hazards_observed": kpis.n_hazards_observed,
    }


def aggregate(runs: list[dict]) -> list[dict]:
    """Agrège les runs par paire (moyenne sur seeds) + score combiné."""
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
        wips = [r["wip_mean"] for r in rs]
        lateness = [r["lateness_mean_days"] for r in rs]
        scrap_yields = [r["yield_pct"] for r in rs]
        # Score combiné : rupture (1-OTIF) + récupération normalisée
        # Plus le score est élevé, plus la paire est destructrice.
        otif_mean = statistics.mean(otifs)
        recovery_mean = statistics.mean(recoveries)
        combined = (1 - otif_mean) + recovery_mean / 30.0
        out.append({
            "pair": pair,
            "domain_a": d_a, "domain_b": d_b,
            "n_seeds": len(rs),
            "otif_mean": otif_mean,
            "otif_sd": (
                statistics.stdev(otifs) if len(otifs) >= 2 else 0.0
            ),
            "recovery_mean_days": recovery_mean,
            "yield_mean": statistics.mean(scrap_yields),
            "wip_mean": statistics.mean(wips),
            "lateness_mean_days": statistics.mean(lateness),
            "combined_impact_score": combined,
        })
    out.sort(
        key=lambda x: x["combined_impact_score"], reverse=True,
    )
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=N_SEEDS_DEFAULT)
    args = parser.parse_args()
    seeds = list(range(42, 42 + args.seeds))
    pairs = all_pairs()
    n_total = len(pairs) * len(seeds)
    print(
        f"Étude paires : {len(pairs)} paires × {len(seeds)} seeds "
        f"= {n_total} runs sur {PILOTAGE} @ saturation {SATURATION}"
    )
    runs = []
    done = crashed = 0
    for d_a, d_b in pairs:
        for seed in seeds:
            r = run_one_pair(d_a, d_b, seed)
            runs.append(r)
            done += 1
            if r.get("status") == "crashed":
                crashed += 1
            if done % 10 == 0:
                print(f"  ... {done}/{n_total} ({crashed} crashs)")

    csv_path = Path("docs/domain_pair_runs.csv")
    all_fields = sorted({k for r in runs for k in r.keys()})
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=all_fields)
        w.writeheader()
        for r in runs:
            w.writerow(r)
    print(f"\nCSV : {csv_path}")

    agg = aggregate(runs)
    json_path = Path("docs/domain_pair_ranking.json")
    json_path.write_text(json.dumps(agg, indent=2, default=str))
    print(f"Ranking : {json_path}\n")

    print("=== Top 10 paires les plus destructrices "
          "(score combiné rupture + récupération) ===\n")
    print(f"{'Rang':<5} {'Paire':<35} {'OTIF':>7} "
          f"{'Recov(j)':>10} {'Yield':>7} {'Score':>8}")
    for i, e in enumerate(agg[:10], start=1):
        print(
            f"{i:<5} {e['pair']:<35} "
            f"{e['otif_mean']:>7.1%} "
            f"{e['recovery_mean_days']:>10.2f} "
            f"{e['yield_mean']:>7.1%} "
            f"{e['combined_impact_score']:>8.4f}"
        )
    print()
    print(f"=== {done - crashed} ok / {crashed} crashs sur {n_total} ===")
    return 0 if crashed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
