"""Étude scientifique stress : 6 pilotages × 6 saturations étendues
× 3 implantations × N seeds avec KPIs QCDS étendus.

Sortie :
  - docs/stress_study_runs.csv      : 1 ligne par run, 14 KPIs
  - docs/stress_study_stats.json    : 5 questions scientifiques
                                       avec stats appariées

Usage :
    python docs/run_stress_study.py [--seeds N]
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

from pilotage_flux.comparative.bce_wire import bce_kpis
from pilotage_flux.comparative.qcds_kpis import (
    compute_robustesse_by_pilotage,
    extract_qcds_kpis,
)
from pilotage_flux.comparative.runner import run_doctrine
from pilotage_flux.comparative.saturation import (
    ROUTING_STRATEGIES,
    ROUTING_STRATEGY_CODE,
    calibrate_scenario_to_saturation,
)
from pilotage_flux.comparative.scenario import (
    DOCTRINES_6_PILOTAGES,
)
from pilotage_flux.comparative.stress_scenario import (
    SATURATION_TARGETS_STRESS,
    stress_scenario,
)
from pilotage_flux.db import db_session


FIXTURES = Path("data/fixtures_extended")
if not FIXTURES.exists():
    FIXTURES = Path("data/fixtures_v1")


def run_one(
    doctrine: str, sat: float, impl: str, seed: int,
) -> dict:
    """Lance un run et collecte les KPIs QCDS étendus."""
    scenario = stress_scenario(seed=seed)
    try:
        scenario = calibrate_scenario_to_saturation(
            scenario, sat, fixtures_dir=FIXTURES,
        )
    except Exception as e:
        return {
            "doctrine": doctrine, "saturation": sat,
            "implantation": impl, "seed": seed,
            "status": "crashed",
            "error": f"calibration: {str(e)[:120]}",
        }

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
                kpis = extract_qcds_kpis(conn, result, scenario)
                bce = (
                    bce_kpis(conn) if doctrine.endswith("_bce")
                    else {"n_decisions_total": 0, "n1_decisions": 0,
                          "n2_decisions": 0, "n3_decisions": 0,
                          "n4_decisions": 0}
                )
        except Exception as e:
            return {
                "doctrine": doctrine, "saturation": sat,
                "implantation": impl, "seed": seed,
                "status": "crashed", "error": str(e)[:120],
            }

    row = {
        "doctrine": doctrine, "saturation": sat,
        "implantation": impl, "seed": seed, "status": "ok",
        **kpis.to_dict(),
        "n_delta_decisions": bce["n_decisions_total"],
        "n1": bce["n1_decisions"], "n2": bce["n2_decisions"],
        "n3": bce["n3_decisions"], "n4": bce["n4_decisions"],
    }
    return row


# ---------------------------------------------------------------------
# Statistiques appariées
# ---------------------------------------------------------------------


def _cliffs_delta(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    n, m = len(a), len(b)
    gt = sum(1 for ai in a for bj in b if ai > bj)
    lt = sum(1 for ai in a for bj in b if ai < bj)
    return (gt - lt) / (n * m)


def _bootstrap_ci_median(
    pairs: list[tuple[float, float]],
    *, n_boot: int = 1000, alpha: float = 0.05, seed: int = 42,
) -> tuple[float, float, float]:
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


def _wilcoxon_pvalue(
    pairs: list[tuple[float, float]],
) -> float | None:
    if not pairs:
        return None
    diffs = [a - b for (a, b) in pairs if (a - b) != 0]
    n = len(diffs)
    if n < 4:
        return None
    abs_diffs = sorted(
        [(abs(d), i) for i, d in enumerate(diffs)], key=lambda x: x[0],
    )
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs_diffs[j + 1][0] == abs_diffs[i][0]:
            j += 1
        avg = (i + j + 2) / 2.0
        for k in range(i, j + 1):
            ranks[abs_diffs[k][1]] = avg
        i = j + 1
    w_plus = sum(r for r, d in zip(ranks, diffs) if d > 0)
    w_minus = sum(r for r, d in zip(ranks, diffs) if d < 0)
    w = min(w_plus, w_minus)
    mean_w = n * (n + 1) / 4.0
    std_w = (n * (n + 1) * (2 * n + 1) / 24.0) ** 0.5
    if std_w == 0:
        return 1.0
    z = (w - mean_w) / std_w
    from math import erf, sqrt
    return 2 * (1 - 0.5 * (1 + erf(abs(z) / sqrt(2))))


def paired_stats(
    runs: list[dict], doctrine_a: str, doctrine_b: str,
    kpi: str, *, label: str | None = None,
    direction_higher_is_better: bool = True,
) -> dict:
    """Apparie runs de a et b par (saturation, implantation, seed)
    et calcule Wilcoxon + bootstrap CI + Cliff's δ."""
    a_index: dict[tuple, float] = {}
    b_index: dict[tuple, float] = {}
    for r in runs:
        if r.get("status") != "ok":
            continue
        if kpi not in r:
            continue
        key = (r["saturation"], r["implantation"], r["seed"])
        if r["doctrine"] == doctrine_a:
            a_index[key] = float(r[kpi])
        elif r["doctrine"] == doctrine_b:
            b_index[key] = float(r[kpi])
    pairs = [(va, b_index[k]) for k, va in a_index.items()
              if k in b_index]
    median, lo, hi = _bootstrap_ci_median(pairs)
    delta = _cliffs_delta([a for a, b in pairs],
                           [b for a, b in pairs])
    p = _wilcoxon_pvalue(pairs)
    # Verdict : direction du gain selon higher_is_better
    if not pairs:
        verdict = "n/a"
    elif p is None:
        verdict = "non testable"
    elif p < 0.05:
        if direction_higher_is_better:
            verdict = ("a > b (significatif)" if median > 0
                       else "a < b (significatif)")
        else:
            verdict = ("a < b (significatif, mieux)" if median < 0
                       else "a > b (significatif, pire)")
    else:
        verdict = "pas de différence significative"
    return {
        "label": label or f"{doctrine_a} vs {doctrine_b}",
        "doctrine_a": doctrine_a, "doctrine_b": doctrine_b,
        "kpi": kpi, "direction_higher_is_better": direction_higher_is_better,
        "n_pairs": len(pairs),
        "median_diff": median, "ci_low": lo, "ci_high": hi,
        "cliffs_delta": delta, "wilcoxon_p": p,
        "verdict": verdict,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=10)
    args = parser.parse_args()

    seeds = list(range(42, 42 + args.seeds))
    n_cells = (
        len(DOCTRINES_6_PILOTAGES) * len(SATURATION_TARGETS_STRESS)
        * len(ROUTING_STRATEGIES) * len(seeds)
    )
    print(f"Étude stress : {n_cells} runs sur {FIXTURES}")

    runs: list[dict] = []
    done = crashed = 0
    for sat in SATURATION_TARGETS_STRESS:
        for impl in ROUTING_STRATEGIES:
            for seed in seeds:
                for doctrine in DOCTRINES_6_PILOTAGES:
                    r = run_one(doctrine, sat, impl, seed)
                    runs.append(r)
                    done += 1
                    if r.get("status") == "crashed":
                        crashed += 1
                    if done % 36 == 0:
                        print(f"  ... {done}/{n_cells} ({crashed} crashs)")

    csv_path = Path("docs/stress_study_runs.csv")
    all_fields = sorted({k for r in runs for k in r.keys()})
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=all_fields)
        w.writeheader()
        for r in runs:
            w.writerow(r)
    print(f"\nCSV : {csv_path}")

    # ---------- 5 questions scientifiques ----------
    questions = []

    print("\n=== Q1. Apport EVENT (OF+EVENT vs OF) ===")
    for kpi, hib, lab in (
        ("otif", True, "OTIF"),
        ("yield_pct", True, "yield"),
        ("wip_mean", False, "WIP moyen"),
        ("lateness_mean_days", False, "retard moyen"),
    ):
        s = paired_stats(runs, "of_event", "of", kpi,
                          label=f"Q1 ({lab})",
                          direction_higher_is_better=hib)
        questions.append(s)
        _print_stat(s)

    print("\n=== Q2. Apport FLUX (FLUX+EVENT vs OF+EVENT) ===")
    for kpi, hib, lab in (
        ("otif", True, "OTIF"),
        ("yield_pct", True, "yield"),
        ("wip_mean", False, "WIP moyen"),
        ("lateness_mean_days", False, "retard moyen"),
    ):
        s = paired_stats(runs, "event", "of_event", kpi,
                          label=f"Q2 ({lab})",
                          direction_higher_is_better=hib)
        questions.append(s)
        _print_stat(s)

    print("\n=== Q3. Apport BCE sur FLUX (FLUX+E+BCE vs FLUX+E) ===")
    for kpi, hib, lab in (
        ("otif", True, "OTIF"),
        ("yield_pct", True, "yield"),
        ("wip_mean", False, "WIP moyen"),
        ("lateness_mean_days", False, "retard moyen"),
        ("mean_recovery_days", False, "récupération"),
    ):
        s = paired_stats(runs, "event_bce", "event", kpi,
                          label=f"Q3 ({lab})",
                          direction_higher_is_better=hib)
        questions.append(s)
        _print_stat(s)

    print("\n=== Q4. Apport BCE sur OF (OF+E+BCE vs OF+E) ===")
    for kpi, hib, lab in (
        ("otif", True, "OTIF"),
        ("yield_pct", True, "yield"),
        ("wip_mean", False, "WIP moyen"),
        ("lateness_mean_days", False, "retard moyen"),
        ("mean_recovery_days", False, "récupération"),
    ):
        s = paired_stats(runs, "of_event_bce", "of_event", kpi,
                          label=f"Q4 ({lab})",
                          direction_higher_is_better=hib)
        questions.append(s)
        _print_stat(s)

    print("\n=== Q5. Robustesse (seuil de rupture OTIF par pilotage) ===")
    rob = compute_robustesse_by_pilotage(runs, kpi_threshold=0.90)
    print(f"  Robustesse (saturation rupture OTIF < 0.90) :")
    for pil in sorted(rob):
        bp = rob[pil]
        s_disp = f"{bp:.2f}" if bp is not None else "robuste partout"
        print(f"    {pil:<18} → {s_disp}")
    questions.append({
        "label": "Q5 robustesse",
        "kpi": "otif breaking point",
        "by_pilotage": rob,
    })

    stats_path = Path("docs/stress_study_stats.json")
    stats_path.write_text(json.dumps(questions, indent=2, default=str))
    print(f"\nStats JSON : {stats_path}")
    print(f"\n=== {done - crashed} ok / {crashed} crashs sur {n_cells} ===")
    return 0 if crashed == 0 else 1


def _print_stat(s: dict) -> None:
    p = s.get("wilcoxon_p")
    p_disp = f"{p:.4f}" if p is not None else "n/a"
    print(
        f"  {s['label']:<28} n={s['n_pairs']:<3} "
        f"Δmedian={s['median_diff']:+.4f} "
        f"CI95=[{s['ci_low']:+.4f},{s['ci_high']:+.4f}] "
        f"δ={s['cliffs_delta']:+.3f} p={p_disp} "
        f"→ {s['verdict']}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
