"""Étude comparative complète : 6 pilotages × 6 saturations × 3
implantations × N seeds, avec battery statistique appariée.

Sortie :
  - docs/bce_full_study_runs.csv    : 1 ligne par run, KPIs bruts
  - docs/bce_full_study_stats.json  : statistiques appariées
                                       (Wilcoxon + bootstrap CI +
                                        Cliff's delta) pour les
                                       paires de pilotages.

Usage :
    python docs/run_bce_full_study.py [--seeds N]
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pilotage_flux.comparative.bce_kpis_advanced import (
    compute_agilite,
    compute_robustesse,
)
from pilotage_flux.comparative.bce_wire import bce_kpis
from pilotage_flux.comparative.runner import run_doctrine
from pilotage_flux.comparative.saturation import (
    ROUTING_STRATEGIES,
    ROUTING_STRATEGY_CODE,
    SATURATION_TARGETS,
    calibrate_scenario_to_saturation,
)
from pilotage_flux.comparative.scenario import (
    DOCTRINES_6_PILOTAGES,
    baseline_scenario,
)
from pilotage_flux.db import db_session


FIXTURES = Path("data/fixtures_extended")
if not FIXTURES.exists():
    FIXTURES = Path("data/fixtures_v1")


def _otif(conn) -> float:
    row = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN status='delivered' OR rejected_at IS NULL "
        "         THEN 1 ELSE 0 END) AS ok "
        "FROM sales_orders"
    ).fetchone()
    if row is None or row["total"] == 0:
        return 0.0
    return float(row["ok"]) / float(row["total"])


def _mean_wip(daily_wip: dict[int, int]) -> float:
    if not daily_wip:
        return 0.0
    return statistics.mean(daily_wip.values())


def _hazard_days(scenario) -> list[int]:
    return sorted({h.day for h in scenario.hazards})


def run_one(
    scenario_base, doctrine: str, sat: float,
    impl: str, seed: int,
) -> dict:
    """Lance un run et collecte tous les KPIs."""
    scenario = replace(scenario_base, seed=seed)
    # Calibrage de saturation
    scenario = calibrate_scenario_to_saturation(
        scenario, sat, fixtures_dir=FIXTURES,
    )
    # Implantation : routing_strategy_code
    impl_code = ROUTING_STRATEGY_CODE[impl]
    param_overrides = {
        ("global", None, "routing_strategy_code"): float(impl_code),
    }

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "run.db"
        try:
            result = run_doctrine(
                scenario, doctrine, db,
                fixtures_dir=FIXTURES,
                evaluate_rejections=False,
                param_overrides=param_overrides,
            )
            with db_session(db) as conn:
                otif = _otif(conn)
                if doctrine.endswith("_bce"):
                    bce = bce_kpis(conn)
                else:
                    bce = {
                        "n_decisions_total": 0,
                        "n1_decisions": 0, "n2_decisions": 0,
                        "n3_decisions": 0, "n4_decisions": 0,
                    }
        except Exception as e:
            return {
                "doctrine": doctrine, "saturation": sat,
                "implantation": impl, "seed": seed,
                "status": "crashed", "error": str(e)[:200],
            }

    hazard_days = _hazard_days(scenario)
    agilite = compute_agilite(result.daily_wip, hazard_days)

    return {
        "doctrine": doctrine,
        "saturation": sat,
        "implantation": impl,
        "seed": seed,
        "status": "ok",
        "otif": otif,
        "mean_wip": _mean_wip(result.daily_wip),
        "n_hazards": len(result.hazards_observed),
        "n_decisions": bce["n_decisions_total"],
        "n1": bce["n1_decisions"],
        "n2": bce["n2_decisions"],
        "n3": bce["n3_decisions"],
        "n4": bce["n4_decisions"],
        "mean_recovery_days": agilite.mean_recovery_days or 0.0,
        "n_recoveries": agilite.n_recoveries_observed,
    }


# ---------------------------------------------------------------------
# Statistiques appariées
# ---------------------------------------------------------------------


def _cliffs_delta(a: list[float], b: list[float]) -> float:
    """Effect size non-paramétrique. Renvoie une valeur dans [-1, 1].

    > 0 : a > b en moyenne, < 0 : b > a, ≈ 0 : pas d'effet.
    Calcul : (#(ai > bj) - #(ai < bj)) / (n × m)
    """
    if not a or not b:
        return 0.0
    n, m = len(a), len(b)
    gt = sum(1 for ai in a for bj in b if ai > bj)
    lt = sum(1 for ai in a for bj in b if ai < bj)
    return (gt - lt) / (n * m)


def _bootstrap_ci_median(
    pairs: list[tuple[float, float]],
    *,
    n_boot: int = 1000,
    alpha: float = 0.05,
    seed: int = 42,
) -> tuple[float, float, float]:
    """Bootstrap percentile pour la médiane des différences.

    Renvoie (median, ci_low, ci_high) à 1-alpha.
    """
    import random
    if not pairs:
        return 0.0, 0.0, 0.0
    diffs = [a - b for (a, b) in pairs]
    median = statistics.median(diffs)
    rng = random.Random(seed)
    means = []
    for _ in range(n_boot):
        sample = [rng.choice(diffs) for _ in range(len(diffs))]
        means.append(statistics.median(sample))
    means.sort()
    lo_idx = int(n_boot * (alpha / 2))
    hi_idx = int(n_boot * (1 - alpha / 2)) - 1
    return median, means[lo_idx], means[hi_idx]


def _wilcoxon_pvalue(pairs: list[tuple[float, float]]) -> float | None:
    """Wilcoxon signed-rank test apparié, p-value bilatérale.

    Implémentation manuelle (sans scipy) pour n ≥ 4. Renvoie None
    si moins de 4 paires non-nulles.
    """
    if not pairs:
        return None
    diffs = [a - b for (a, b) in pairs if (a - b) != 0]
    n = len(diffs)
    if n < 4:
        return None

    # Rangs des |diff|
    abs_diffs = sorted(
        [(abs(d), i) for i, d in enumerate(diffs)], key=lambda x: x[0],
    )
    ranks = [0.0] * n
    # Moyenne des rangs en cas d'ex aequo
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs_diffs[j + 1][0] == abs_diffs[i][0]:
            j += 1
        avg_rank = (i + j + 2) / 2.0   # rangs 1-based
        for k in range(i, j + 1):
            ranks[abs_diffs[k][1]] = avg_rank
        i = j + 1

    w_plus = sum(r for r, d in zip(ranks, diffs) if d > 0)
    w_minus = sum(r for r, d in zip(ranks, diffs) if d < 0)
    w = min(w_plus, w_minus)

    # Approximation normale (valable pour n ≥ 10 mais raisonnable
    # pour n ≥ 4 en simulation)
    mean_w = n * (n + 1) / 4.0
    std_w = (n * (n + 1) * (2 * n + 1) / 24.0) ** 0.5
    if std_w == 0:
        return 1.0
    z = (w - mean_w) / std_w
    # P-value bilatérale via normale standard
    from math import erf, sqrt
    pval = 2 * (1 - 0.5 * (1 + erf(abs(z) / sqrt(2))))
    return pval


def paired_stats(
    runs: list[dict],
    doctrine_a: str,
    doctrine_b: str,
    kpi: str = "otif",
) -> dict:
    """Apparie runs de a et b par (saturation, implantation, seed).

    Renvoie {n_pairs, median_diff, ci_low, ci_high, wilcoxon_p,
             cliffs_delta}.
    """
    a_index: dict[tuple, float] = {}
    b_index: dict[tuple, float] = {}
    for r in runs:
        if r.get("status") != "ok":
            continue
        key = (r["saturation"], r["implantation"], r["seed"])
        if r["doctrine"] == doctrine_a:
            a_index[key] = r[kpi]
        elif r["doctrine"] == doctrine_b:
            b_index[key] = r[kpi]

    pairs = []
    for k, va in a_index.items():
        if k in b_index:
            pairs.append((va, b_index[k]))

    median, lo, hi = _bootstrap_ci_median(pairs)
    delta = _cliffs_delta(
        [a for a, b in pairs], [b for a, b in pairs],
    )
    p = _wilcoxon_pvalue(pairs)

    return {
        "doctrine_a": doctrine_a,
        "doctrine_b": doctrine_b,
        "kpi": kpi,
        "n_pairs": len(pairs),
        "median_diff_a_minus_b": median,
        "ci_low_95pct": lo,
        "ci_high_95pct": hi,
        "wilcoxon_pvalue": p,
        "cliffs_delta": delta,
    }


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=3,
                        help="Nb de seeds par cellule (default 3).")
    args = parser.parse_args()

    seeds = list(range(42, 42 + args.seeds))
    n_cells = (
        len(DOCTRINES_6_PILOTAGES) * len(SATURATION_TARGETS)
        * len(ROUTING_STRATEGIES) * len(seeds)
    )
    print(f"Étude {n_cells} runs sur fixtures={FIXTURES}")

    scenario_base = baseline_scenario()
    runs: list[dict] = []
    done = 0
    crashed = 0
    for sat in SATURATION_TARGETS:
        for impl in ROUTING_STRATEGIES:
            for seed in seeds:
                for doctrine in DOCTRINES_6_PILOTAGES:
                    r = run_one(scenario_base, doctrine, sat, impl, seed)
                    runs.append(r)
                    done += 1
                    if r.get("status") == "crashed":
                        crashed += 1
                    if done % 18 == 0:
                        print(
                            f"  ... {done}/{n_cells} "
                            f"({crashed} crashs)"
                        )

    # CSV
    csv_path = Path("docs/bce_full_study_runs.csv")
    fields = list(runs[0].keys()) if runs else []
    # Garantit que les KPI columns existent même si premier run crashed
    all_fields = sorted({k for r in runs for k in r.keys()})
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=all_fields)
        writer.writeheader()
        for r in runs:
            writer.writerow(r)
    print(f"\nCSV : {csv_path}")

    # Stats appariés : 4 comparaisons doctrinales clés
    pairs_to_test = [
        ("of_event", "of", "apport EVENT sur OF"),
        ("event", "flux", "apport EVENT sur FLUX"),
        ("of_event_bce", "of_event", "apport BCE sur OF+EVENT"),
        ("event_bce", "event", "apport BCE sur FLUX+EVENT"),
        ("flux", "of", "apport FLUX (vs OF baseline)"),
    ]
    stats = []
    for da, db, lbl in pairs_to_test:
        s = paired_stats(runs, da, db, kpi="otif")
        s["label"] = lbl
        stats.append(s)
        print(
            f"\n{lbl}: n={s['n_pairs']} "
            f"median(Δ)={s['median_diff_a_minus_b']:+.4f} "
            f"95%CI=[{s['ci_low_95pct']:+.4f}, {s['ci_high_95pct']:+.4f}] "
            f"Cliff's δ={s['cliffs_delta']:+.3f} "
            f"Wilcoxon p={s['wilcoxon_pvalue']}"
        )

    stats_path = Path("docs/bce_full_study_stats.json")
    stats_path.write_text(json.dumps(stats, indent=2, default=str))
    print(f"\nStats JSON : {stats_path}")
    print(f"\n=== {done - crashed} ok / {crashed} crashs sur {n_cells} ===")
    return 0 if crashed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
