"""Flux matière (L6.2 — famille 2/5).

Vue agrégée des mouvements matière : stocks courants, achats ouverts,
consommations réelles, écarts conso vs BOM théorique.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass
class MaterialFlowItem:
    article_id: str
    qty_on_hand: float
    qty_reserved: float
    qty_on_order: float          # somme des PO ouverts/partiels
    qty_consumed: float          # somme des conso réelles
    qty_theoretical: float       # BOM × OF.quantity (planifié)
    qty_gap: float               # consommé - théorique
    open_purchase_orders: int


@dataclass
class MaterialFlowReport:
    items: list[MaterialFlowItem] = field(default_factory=list)

    @property
    def total_consumed(self) -> float:
        return sum(i.qty_consumed for i in self.items)

    @property
    def total_gap(self) -> float:
        return sum(i.qty_gap for i in self.items)


def material_flow_view(conn: sqlite3.Connection) -> MaterialFlowReport:
    """Agrège stocks + PO + consommations + théorique BOM par article.

    Sources :
      - `stocks` : qty_available, qty_reserved
      - `purchase_orders` : PO ouverts/partiels
      - `mes_consumptions` : conso réelles
      - `bom_lines` × `manufacturing_orders.quantity` : conso théorique
    """
    report = MaterialFlowReport()
    articles = conn.execute(
        "SELECT article_id FROM articles ORDER BY article_id ASC"
    ).fetchall()
    for art in articles:
        aid = art["article_id"]
        stk = conn.execute(
            "SELECT qty_available, qty_reserved FROM stocks WHERE article_id = ?",
            (aid,),
        ).fetchone()
        qty_oh = float(stk["qty_available"]) if stk else 0.0
        qty_res = float(stk["qty_reserved"]) if stk else 0.0

        po = conn.execute(
            """
            SELECT COALESCE(SUM(qty_ordered - qty_received), 0) AS qty_open,
                   COUNT(*) AS n
            FROM purchase_orders
            WHERE article_id = ? AND status IN ('open', 'partial')
            """,
            (aid,),
        ).fetchone()
        qty_on_order = float(po["qty_open"]) if po else 0.0
        n_po = int(po["n"]) if po else 0

        cons = conn.execute(
            "SELECT COALESCE(SUM(qty_consumed), 0) AS s FROM mes_consumptions "
            "WHERE article_id = ?",
            (aid,),
        ).fetchone()
        qty_consumed = float(cons["s"]) if cons else 0.0

        theo = conn.execute(
            """
            SELECT COALESCE(SUM(bl.quantity * mo.quantity), 0) AS s
            FROM bom_lines bl
            JOIN manufacturing_orders mo ON mo.article_id = bl.parent_article
            WHERE bl.child_article = ? AND mo.status != 'cancelled'
            """,
            (aid,),
        ).fetchone()
        qty_theo = float(theo["s"]) if theo else 0.0

        report.items.append(MaterialFlowItem(
            article_id=aid,
            qty_on_hand=qty_oh,
            qty_reserved=qty_res,
            qty_on_order=qty_on_order,
            qty_consumed=qty_consumed,
            qty_theoretical=qty_theo,
            qty_gap=qty_consumed - qty_theo,
            open_purchase_orders=n_po,
        ))
    return report
