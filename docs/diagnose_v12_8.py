"""V12.8 — Vérification : CPM + queueing data-driven sans safety_factor magique.

Compare FLUX V11 (baseline) vs FLUX V12.8 (CPM+Little) vs FLUX V12.7
(safety_factor=150 manuel) sur baseline_xl seed=42.

V12.8 doit fermer la quantity_compliance sans constante magique.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from pilotage_flux.comparative.runner import run_doctrine
from pilotage_flux.comparative.scenario import (
    DOCTRINE_FLUX, DOCTRINE_OF, baseline_xl_scenario, jitter_scenario,
)
from pilotage_flux.db import db_session


def trace(db_path: Path, label: str) -> dict:
    with db_session(db_path) as conn:
        row = conn.execute(
            "SELECT SUM(quantity) AS t FROM sales_orders WHERE rejected_at IS NULL"
        ).fetchone()
        so = float(row["t"] or 0)
        row = conn.execute(
            "SELECT SUM(qty_good) AS t FROM manufacturing_orders "
            "WHERE article_id IN (SELECT article_id FROM sales_orders)"
        ).fetchone()
        good = float(row["t"] or 0)
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM manufacturing_orders "
            "WHERE status = 'in_progress' "
            "AND article_id IN (SELECT article_id FROM sales_orders)"
        ).fetchone()
        stuck = int(row["n"] or 0)
    return {"label": label, "so": so, "good": good, "stuck": stuck}


def main() -> None:
    print("=== V12.8 : CPM + Little vs V12.7 magic + V11 baseline ===\n")
    fixtures = Path("data/fixtures_extended")
    with TemporaryDirectory(prefix="v128_") as tmp:
        work = Path(tmp)
        scen = jitter_scenario(baseline_xl_scenario(), seed=42)

        db_of = work / "of.db"
        run_doctrine(scen, DOCTRINE_OF, db_of, fixtures_dir=fixtures,
                     evaluate_rejections=False)
        of_d = trace(db_of, "OF")

        db_v11 = work / "v11.db"
        run_doctrine(scen, DOCTRINE_FLUX, db_v11, fixtures_dir=fixtures,
                     evaluate_rejections=False)
        v11_d = trace(db_v11, "FLUX V11")

        db_v127 = work / "v127.db"
        run_doctrine(scen, DOCTRINE_FLUX, db_v127, fixtures_dir=fixtures,
                     evaluate_rejections=False,
                     param_overrides={
                         ("global", None, "smoothing_horizon_aware"): 1.0,
                         ("global", None,
                          "smoothing_horizon_safety_factor"): 150.0,
                     })
        v127_d = trace(db_v127, "FLUX V12.7 (sf=150)")

        db_v128 = work / "v128.db"
        run_doctrine(scen, DOCTRINE_FLUX, db_v128, fixtures_dir=fixtures,
                     evaluate_rejections=False,
                     param_overrides={
                         ("global", None, "smoothing_cpm_aware"): 1.0,
                     })
        v128_d = trace(db_v128, "FLUX V12.8 (CPM+Little)")

        db_v128s = work / "v128_slack.db"
        run_doctrine(scen, DOCTRINE_FLUX, db_v128s, fixtures_dir=fixtures,
                     evaluate_rejections=False,
                     param_overrides={
                         ("global", None, "smoothing_cpm_aware"): 1.0,
                         ("global", None, "smoothing_slack_ordering"): 1.0,
                     })
        v128s_d = trace(db_v128s, "FLUX V12.8 + SLACK")

        db_v128b = work / "v128_bom.db"
        run_doctrine(scen, DOCTRINE_FLUX, db_v128b, fixtures_dir=fixtures,
                     evaluate_rejections=False,
                     param_overrides={
                         ("global", None, "smoothing_bom_topo"): 1.0,
                     })
        v128b_d = trace(db_v128b, "FLUX V12.8 BOM-topo")

        db_v128bc = work / "v128_bom_cpm.db"
        run_doctrine(scen, DOCTRINE_FLUX, db_v128bc, fixtures_dir=fixtures,
                     evaluate_rejections=False,
                     param_overrides={
                         ("global", None, "smoothing_bom_topo"): 1.0,
                         ("global", None, "smoothing_cpm_aware"): 1.0,
                     })
        v128bc_d = trace(db_v128bc, "FLUX V12.8 BOM+CPM")

        db_v128f = work / "v128_full.db"
        run_doctrine(scen, DOCTRINE_FLUX, db_v128f, fixtures_dir=fixtures,
                     evaluate_rejections=False,
                     param_overrides={
                         ("global", None, "smoothing_bom_topo"): 1.0,
                         ("global", None, "smoothing_cpm_aware"): 1.0,
                         ("global", None, "smoothing_queueing_rho_cap"): 0.99,
                     })
        v128f_d = trace(db_v128f, "FLUX V12.8 BOM+CPM ρ=0.99")

    print(f"{'Doctrine':30} {'good/so':>12} {'stuck':>6}")
    print("-" * 50)
    for d in [of_d, v11_d, v127_d, v128_d, v128s_d, v128b_d, v128bc_d, v128f_d]:
        ratio = d["good"] / d["so"] if d["so"] > 0 else 0
        print(f"{d['label']:30} {ratio*100:>11.1f}% {d['stuck']:>6}")


if __name__ == "__main__":
    main()
