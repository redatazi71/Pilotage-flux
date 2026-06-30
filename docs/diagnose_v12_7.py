"""V12.7 — Diagnostic du défaut Q de FLUX/EVENT.

Trace la quantité à 4 étapes pour OF et FLUX sur baseline_xl :

  1. SO.quantity : demande client totale
  2. candidate.quantity : ce que CBN décide de produire (FLUX seul)
  3. manufacturing_orders.quantity : ce qui est lancé en OF
  4. manufacturing_orders.qty_good : ce qui est effectivement produit

Identifie l'étape où FLUX perd 31 % vs OF 5 %.
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
    """Lit la DB après run et trace les quantités à chaque étape."""
    with db_session(db_path) as conn:
        # 1. Total demandé via sales_orders
        row = conn.execute(
            "SELECT SUM(quantity) AS total FROM sales_orders "
            "WHERE rejected_at IS NULL"
        ).fetchone()
        so_total = float(row["total"] or 0)

        # 2. Total candidate.quantity (FLUX seul)
        row = conn.execute(
            "SELECT SUM(quantity) AS total FROM candidate_orders"
        ).fetchone()
        cand_total = float(row["total"] or 0)

        # 3. Total manufacturing_orders.quantity
        row = conn.execute(
            "SELECT SUM(quantity) AS total FROM manufacturing_orders "
            "WHERE article_id IN (SELECT article_id FROM sales_orders)"
        ).fetchone()
        of_qty_total = float(row["total"] or 0)

        # 4. Total qty_good livrée
        row = conn.execute(
            "SELECT SUM(qty_good) AS total FROM manufacturing_orders "
            "WHERE article_id IN (SELECT article_id FROM sales_orders)"
        ).fetchone()
        of_qty_good_total = float(row["total"] or 0)

        # 5. Total qty_scrap
        row = conn.execute(
            "SELECT SUM(qty_scrap) AS total FROM manufacturing_orders "
            "WHERE article_id IN (SELECT article_id FROM sales_orders)"
        ).fetchone()
        of_qty_scrap_total = float(row["total"] or 0)

        # Quelques OFs détaillés
        sample = conn.execute(
            """
            SELECT m.of_id, m.status, m.quantity, m.qty_good, m.qty_scrap,
                   a.label AS article
            FROM manufacturing_orders m
            JOIN articles a ON a.article_id = m.article_id
            WHERE m.article_id IN (SELECT article_id FROM sales_orders)
            ORDER BY m.of_id
            LIMIT 8
            """
        ).fetchall()

    return {
        "label": label,
        "so_total": so_total,
        "cand_total": cand_total,
        "of_qty_total": of_qty_total,
        "of_qty_good_total": of_qty_good_total,
        "of_qty_scrap_total": of_qty_scrap_total,
        "sample": [dict(r) for r in sample],
    }


def main() -> None:
    print("=== V12.7 Diagnostic : trace de quantité baseline_xl ===\n")
    fixtures_dir = Path("data/fixtures_extended")

    with TemporaryDirectory(prefix="v12_7_diag_") as tmp:
        work = Path(tmp)
        scen = jitter_scenario(baseline_xl_scenario(), seed=42)

        # Run OF
        db_of = work / "of.db"
        run_doctrine(scen, DOCTRINE_OF, db_of,
                      fixtures_dir=fixtures_dir,
                      evaluate_rejections=False)
        of_data = trace_quantities(db_of, "OF")

        # Run FLUX
        db_flux = work / "flux.db"
        run_doctrine(scen, DOCTRINE_FLUX, db_flux,
                      fixtures_dir=fixtures_dir,
                      evaluate_rejections=False)
        flux_data = trace_quantities(db_flux, "FLUX")

    print("Étape par étape (quantité totale articles finis) :\n")
    print(f"{'Étape':45} {'OF':>15} {'FLUX':>15}  {'Ratio FLUX/OF':>15}")
    print("-" * 95)
    for key, label in [
        ("so_total", "1. SO.quantity (demande client)"),
        ("cand_total", "2. candidate_orders.quantity"),
        ("of_qty_total", "3. manufacturing_orders.quantity"),
        ("of_qty_good_total", "4. qty_good livré"),
        ("of_qty_scrap_total", "5. qty_scrap"),
    ]:
        of_val = of_data[key]
        flux_val = flux_data[key]
        ratio = flux_val / of_val if of_val > 0 else 0
        print(f"{label:45} {of_val:>15.0f} {flux_val:>15.0f}  {ratio:>14.3f}")

    # Ratios par doctrine
    print("\nRatios internes par doctrine :")
    for d_name, d in [("OF", of_data), ("FLUX", flux_data)]:
        print(f"\n  {d_name} :")
        if d["so_total"] > 0:
            print(f"    cand/so      = {d['cand_total']/d['so_total']:.3f}")
            print(f"    of_qty/so    = {d['of_qty_total']/d['so_total']:.3f}")
            print(f"    qty_good/so  = {d['of_qty_good_total']/d['so_total']:.3f}")
            if d["of_qty_total"] > 0:
                print(f"    qty_good/of  = {d['of_qty_good_total']/d['of_qty_total']:.3f}")
                print(f"    scrap/of     = {d['of_qty_scrap_total']/d['of_qty_total']:.3f}")

    # Échantillon OFs
    print("\n--- Échantillon 8 premiers OFs ---")
    for d_name, d in [("OF", of_data), ("FLUX", flux_data)]:
        print(f"\n  Doctrine {d_name} :")
        print(f"    {'of_id':<12} {'status':<12} {'qty':>6} {'good':>6} {'scrap':>6}  article")
        for of in d["sample"]:
            print(
                f"    {of['of_id']:<12} {of['status']:<12} "
                f"{of['quantity']:>6.0f} {of['qty_good']:>6.0f} "
                f"{of['qty_scrap']:>6.0f}  {of['article']}"
            )


if __name__ == "__main__":
    main()
