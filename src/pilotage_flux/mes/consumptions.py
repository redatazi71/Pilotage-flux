"""Consommations matiere reelles (V2).

Chaque declaration de consommation :
  - Insert mes_consumptions (consumption_id, of_id, of_op_id, article_id, qty)
  - Decremente stocks.qty_available pour l'article achete
  - Emit event OP_STARTED-like ? Non : on a deja les events op/cloture.

Ecarts = somme reelle - theorique (BOM × OF.quantity).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pilotage_flux.stocks_purchasing import get_stock, set_stock


@dataclass(frozen=True)
class Consumption:
    consumption_id: int
    of_id: str
    of_op_id: int | None
    article_id: str
    qty_consumed: float
    at_time: str
    note: str | None


@dataclass(frozen=True)
class ConsumptionGap:
    of_id: str
    article_id: str
    qty_theoretical: float
    qty_real: float
    gap: float
    gap_ratio: float  # gap / qty_theoretical


def declare_consumption(
    conn: sqlite3.Connection,
    *,
    of_id: str,
    article_id: str,
    qty_consumed: float,
    of_op_id: int | None = None,
    note: str | None = None,
) -> Consumption:
    """Declare une consommation reelle et decremente le stock disponible."""
    if qty_consumed <= 0:
        raise ValueError("qty_consumed doit etre strictement positif")
    of_row = conn.execute(
        "SELECT of_id FROM manufacturing_orders WHERE of_id = ?", (of_id,)
    ).fetchone()
    if of_row is None:
        raise ValueError(f"OF inconnu : {of_id}")
    art_row = conn.execute(
        "SELECT is_purchased FROM articles WHERE article_id = ?", (article_id,)
    ).fetchone()
    if art_row is None:
        raise ValueError(f"Article inconnu : {article_id}")

    cur = conn.execute(
        """
        INSERT INTO mes_consumptions
            (of_id, of_op_id, article_id, qty_consumed, note)
        VALUES (?, ?, ?, ?, ?)
        """,
        (of_id, of_op_id, article_id, qty_consumed, note),
    )
    cid = cur.lastrowid
    assert cid is not None

    # Decrementation stock disponible (le composant est consume reellement)
    current = get_stock(conn, article_id)
    new_qty = max(0.0, current.qty_available - qty_consumed)
    set_stock(conn, article_id, new_qty)
    # Preserve la reservation
    conn.execute(
        "UPDATE stocks SET qty_reserved = ? WHERE article_id = ?",
        (current.qty_reserved, article_id),
    )

    row = conn.execute(
        "SELECT * FROM mes_consumptions WHERE consumption_id = ?", (cid,)
    ).fetchone()
    return Consumption(
        consumption_id=int(row["consumption_id"]),
        of_id=row["of_id"],
        of_op_id=int(row["of_op_id"]) if row["of_op_id"] is not None else None,
        article_id=row["article_id"],
        qty_consumed=float(row["qty_consumed"]),
        at_time=row["at_time"],
        note=row["note"],
    )


def list_consumptions(
    conn: sqlite3.Connection,
    *,
    of_id: str | None = None,
    article_id: str | None = None,
) -> list[Consumption]:
    sql = "SELECT * FROM mes_consumptions WHERE 1=1"
    params: list[str] = []
    if of_id is not None:
        sql += " AND of_id = ?"
        params.append(of_id)
    if article_id is not None:
        sql += " AND article_id = ?"
        params.append(article_id)
    sql += " ORDER BY consumption_id ASC"
    rows = conn.execute(sql, params).fetchall()
    return [
        Consumption(
            consumption_id=int(r["consumption_id"]),
            of_id=r["of_id"],
            of_op_id=int(r["of_op_id"]) if r["of_op_id"] is not None else None,
            article_id=r["article_id"],
            qty_consumed=float(r["qty_consumed"]),
            at_time=r["at_time"],
            note=r["note"],
        )
        for r in rows
    ]


def compute_consumption_gaps(
    conn: sqlite3.Connection, of_id: str
) -> list[ConsumptionGap]:
    """Calcule les ecarts matiere pour un OF : reel vs theorique BOM × qty.

    Le theorique utilise les flattened_bom_lines si presentes, sinon
    bom_lines mono-niveau.
    """
    of_row = conn.execute(
        "SELECT article_id, quantity FROM manufacturing_orders WHERE of_id = ?",
        (of_id,),
    ).fetchone()
    if of_row is None:
        raise ValueError(f"OF inconnu : {of_id}")
    of_qty = float(of_row["quantity"])
    of_article = of_row["article_id"]

    # Theorique : essai sur flattened_bom_lines (composants feuilles uniquement)
    flat_rows = conn.execute(
        """
        SELECT component_article, cumulative_quantity
        FROM flattened_bom_lines
        WHERE root_article = ? AND is_leaf = 1
        """,
        (of_article,),
    ).fetchall()
    if not flat_rows:
        flat_rows = conn.execute(
            "SELECT child_article AS component_article, quantity AS cumulative_quantity "
            "FROM bom_lines WHERE parent_article = ?",
            (of_article,),
        ).fetchall()

    theoretical: dict[str, float] = {
        r["component_article"]: float(r["cumulative_quantity"]) * of_qty
        for r in flat_rows
    }

    # Reel : somme consommations
    real_rows = conn.execute(
        """
        SELECT article_id, COALESCE(SUM(qty_consumed), 0) AS q
        FROM mes_consumptions WHERE of_id = ?
        GROUP BY article_id
        """,
        (of_id,),
    ).fetchall()
    real: dict[str, float] = {r["article_id"]: float(r["q"]) for r in real_rows}

    articles = sorted(set(theoretical) | set(real))
    out: list[ConsumptionGap] = []
    for art in articles:
        th = theoretical.get(art, 0.0)
        re = real.get(art, 0.0)
        gap = re - th
        ratio = (gap / th) if th > 0 else 0.0
        out.append(
            ConsumptionGap(
                of_id=of_id,
                article_id=art,
                qty_theoretical=th,
                qty_real=re,
                gap=gap,
                gap_ratio=ratio,
            )
        )
    return out
