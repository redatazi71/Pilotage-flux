"""V12.7 — Vérification : V12.7 corrige-t-il bien les OFs stuck `in_progress` ?

Re-trace les quantités sur baseline_xl en activant smoothing_horizon_aware.
Compare FLUX V11 (baseline défaillant) vs FLUX V12.7 (correction).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from pilotage_flux.comparative.runner import run_doctrine
from pilotage_flux.comparative.scenario import (
    DOCTRINE_FLUX, DOCTRINE_OF,
    baseline_xl_scenario,
    jitter_scenario,
)
from pilotage_flux.db import db_session


def trace_quantities(db_path: Path, label: str) -> dict:
    with db_session(db_path) as conn:
        row = conn.execute(
            "SELECT SUM(quantity) AS total FROM sales_orders "
            "WHERE rejected_at IS NULL"
        ).fetchone()
        so_total = float(row["total"] or 0)

        row = conn.execute(
            "SELECT SUM(quantity) AS total FROM candidate_orders"
        ).fetchone()
        cand_total = float(row["total"] or 0)

        row = conn.execute(
            "SELECT SUM(quantity) AS total FROM manufacturing_orders "
            "WHERE article_id IN (SELECT article_id FROM sales_orders)"
        ).fetchone()
        of_qty_total = float(row["total"] or 0)

        row = conn.execute(
            "SELECT SUM(qty_good) AS total FROM manufacturing_orders "
            "WHERE article_id IN (SELECT article_id FROM sales_orders)"
        ).fetchone()
        of_qty_good_total = float(row["total"] or 0)

        # Count OFs stuck in_progress
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM manufacturing_orders "
            "WHERE status = 'in_progress' "
            "AND article_id IN (SELECT article_id FROM sales_orders)"
        ).fetchone()
        n_stuck = int(row["n"] or 0)

    return {
        "label": label,
        "so_total": so_total,
        "cand_total": cand_total,
        "of_qty_total": of_qty_total,
        "of_qty_good_total": of_qty_good_total,
        "n_stuck": n_stuck,
    }


def main() -> None:
    print("=== V12.7 fix verification : FLUX V11 vs FLUX V12.7 ===\n")
    fixtures_dir = Path("data/fixtures_extended")

    with TemporaryDirectory(prefix="v12_7_fix_") as tmp:
        work = Path(tmp)
        scen = jitter_scenario(baseline_xl_scenario(), seed=42)

        db_of = work / "of.db"
        run_doctrine(scen, DOCTRINE_OF, db_of,
                     fixtures_dir=fixtures_dir,
                     evaluate_rejections=False)
        of_data = trace_quantities(db_of, "OF (ref)")

        db_flux_v11 = work / "flux_v11.db"
        run_doctrine(scen, DOCTRINE_FLUX, db_flux_v11,
                     fixtures_dir=fixtures_dir,
                     evaluate_rejections=False)
        flux_v11 = trace_quantities(db_flux_v11, "FLUX V11")

        db_flux_v127 = work / "flux_v127.db"
        run_doctrine(scen, DOCTRINE_FLUX, db_flux_v127,
                     fixtures_dir=fixtures_dir,
                     evaluate_rejections=False,
                     param_overrides={
                         ("global", None, "smoothing_horizon_aware"): 1.0,
                         ("global", None,
                          "smoothing_horizon_safety_factor"): 150.0,
                     })
        flux_v127 = trace_quantities(db_flux_v127, "FLUX V12.7 (sf=150)")

    print(f"{'Étape':40} {'OF':>12} {'FLUX V11':>12} {'FLUX V12.7':>12}")
    print("-" * 78)
    for key, label in [
        ("so_total", "1. SO.quantity"),
        ("cand_total", "2. candidate_orders.quantity"),
        ("of_qty_total", "3. OF.quantity"),
        ("of_qty_good_total", "4. qty_good livré"),
        ("n_stuck", "5. OFs stuck `in_progress`"),
    ]:
        print(
            f"{label:40} {of_data[key]:>12.0f} "
            f"{flux_v11[key]:>12.0f} {flux_v127[key]:>12.0f}"
        )

    print("\nRatios qty_good / SO :")
    for d in [of_data, flux_v11, flux_v127]:
        if d["so_total"] > 0:
            ratio = d["of_qty_good_total"] / d["so_total"]
            print(f"  {d['label']:15} : {ratio:.3f} ({ratio*100:.1f} %)")


if __name__ == "__main__":
    main()
