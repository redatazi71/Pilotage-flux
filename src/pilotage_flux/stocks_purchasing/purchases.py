"""Achats ouverts (purchase_orders) - V2.

Chaque PO projette une arrivee future de qty_ordered unites d'un article.
A la reception, on incremente qty_received et qty_available du stock.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class PurchaseOrder:
    po_id: str
    article_id: str
    qty_ordered: float
    qty_received: float
    expected_at: str | None
    status: str
    supplier_ref: str | None
    created_at: str
    received_at: str | None


def _row(row: sqlite3.Row) -> PurchaseOrder:
    return PurchaseOrder(
        po_id=row["po_id"],
        article_id=row["article_id"],
        qty_ordered=float(row["qty_ordered"]),
        qty_received=float(row["qty_received"]),
        expected_at=row["expected_at"],
        status=row["status"],
        supplier_ref=row["supplier_ref"],
        created_at=row["created_at"],
        received_at=row["received_at"],
    )


def _next_po_id(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT po_id FROM purchase_orders ORDER BY po_id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return "PO-0001"
    last = row["po_id"]
    try:
        n = int(last.split("-")[-1])
    except (ValueError, IndexError):
        n = 0
    return f"PO-{n + 1:04d}"


def create_purchase(
    conn: sqlite3.Connection,
    *,
    article_id: str,
    qty_ordered: float,
    expected_at: str | None = None,
    supplier_ref: str | None = None,
) -> PurchaseOrder:
    if qty_ordered <= 0:
        raise ValueError("qty_ordered doit etre strictement positif")
    art = conn.execute(
        "SELECT is_purchased FROM articles WHERE article_id = ?", (article_id,)
    ).fetchone()
    if art is None:
        raise ValueError(f"Article inconnu : {article_id}")

    po_id = _next_po_id(conn)
    conn.execute(
        """
        INSERT INTO purchase_orders
            (po_id, article_id, qty_ordered, expected_at, supplier_ref)
        VALUES (?, ?, ?, ?, ?)
        """,
        (po_id, article_id, qty_ordered, expected_at, supplier_ref),
    )
    row = conn.execute(
        "SELECT * FROM purchase_orders WHERE po_id = ?", (po_id,)
    ).fetchone()
    return _row(row)


def receive_purchase(
    conn: sqlite3.Connection,
    po_id: str,
    *,
    qty_received: float,
) -> PurchaseOrder:
    """Receptionne une quantite (totale ou partielle) sur un PO.

    Met a jour qty_received + status (partial/received) + qty_available du stock.
    """
    if qty_received <= 0:
        raise ValueError("qty_received doit etre strictement positif")
    row = conn.execute(
        "SELECT * FROM purchase_orders WHERE po_id = ?", (po_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"PO inconnu : {po_id}")
    if row["status"] in ("received", "cancelled"):
        raise ValueError(
            f"PO {po_id} en statut {row['status']!r} : reception impossible"
        )

    new_received = float(row["qty_received"]) + qty_received
    if new_received > float(row["qty_ordered"]):
        raise ValueError(
            f"Reception {qty_received} depasse le reste a recevoir "
            f"({float(row['qty_ordered']) - float(row['qty_received'])})"
        )
    new_status = "received" if new_received >= float(row["qty_ordered"]) else "partial"
    conn.execute(
        """
        UPDATE purchase_orders
        SET qty_received = ?, status = ?,
            received_at = CASE WHEN ? = 'received' THEN datetime('now') ELSE received_at END
        WHERE po_id = ?
        """,
        (new_received, new_status, new_status, po_id),
    )

    # Augmente le stock disponible
    from pilotage_flux.stocks_purchasing.stocks import get_stock, set_stock
    current = get_stock(conn, row["article_id"])
    set_stock(conn, row["article_id"], current.qty_available + qty_received)
    # NB: set_stock reset qty_reserved a 0 dans cette version simple.
    # On preserve la reservation existante :
    conn.execute(
        "UPDATE stocks SET qty_reserved = ? WHERE article_id = ?",
        (current.qty_reserved, row["article_id"]),
    )

    new_row = conn.execute(
        "SELECT * FROM purchase_orders WHERE po_id = ?", (po_id,)
    ).fetchone()
    return _row(new_row)


def cancel_purchase(
    conn: sqlite3.Connection, po_id: str, *, reason: str | None = None
) -> PurchaseOrder:
    row = conn.execute(
        "SELECT status FROM purchase_orders WHERE po_id = ?", (po_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"PO inconnu : {po_id}")
    if row["status"] == "received":
        raise ValueError(f"PO {po_id} deja receptionne integralement")
    conn.execute(
        "UPDATE purchase_orders SET status = 'cancelled' WHERE po_id = ?",
        (po_id,),
    )
    new_row = conn.execute(
        "SELECT * FROM purchase_orders WHERE po_id = ?", (po_id,)
    ).fetchone()
    return _row(new_row)


def list_purchases(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    article_id: str | None = None,
) -> list[PurchaseOrder]:
    sql = "SELECT * FROM purchase_orders WHERE 1=1"
    params: list[str] = []
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    if article_id is not None:
        sql += " AND article_id = ?"
        params.append(article_id)
    sql += " ORDER BY po_id ASC"
    return [_row(r) for r in conn.execute(sql, params)]


def open_qty(conn: sqlite3.Connection, article_id: str) -> float:
    """Quantite restant a recevoir pour un article (open + partial)."""
    row = conn.execute(
        """
        SELECT COALESCE(SUM(qty_ordered - qty_received), 0) AS q
        FROM purchase_orders
        WHERE article_id = ? AND status IN ('open', 'partial')
        """,
        (article_id,),
    ).fetchone()
    return float(row["q"]) if row else 0.0
