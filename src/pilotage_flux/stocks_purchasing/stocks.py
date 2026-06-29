"""Stocks V2 : 1 niveau, qty_available + qty_reserved.

Pas de gestion par lot ou serial en V2 — le pegging fait au niveau article.
La quantite projetee = qty_available - qty_reserved + somme open PO.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class StockLevel:
    article_id: str
    qty_available: float
    qty_reserved: float
    updated_at: str

    @property
    def qty_free(self) -> float:
        return self.qty_available - self.qty_reserved


def _row(row: sqlite3.Row) -> StockLevel:
    return StockLevel(
        article_id=row["article_id"],
        qty_available=float(row["qty_available"]),
        qty_reserved=float(row["qty_reserved"]),
        updated_at=row["updated_at"],
    )


def get_stock(conn: sqlite3.Connection, article_id: str) -> StockLevel:
    """Renvoie le niveau de stock courant pour un article (0/0 si absent)."""
    row = conn.execute(
        "SELECT * FROM stocks WHERE article_id = ?", (article_id,)
    ).fetchone()
    if row is None:
        return StockLevel(
            article_id=article_id,
            qty_available=0.0,
            qty_reserved=0.0,
            updated_at="",
        )
    return _row(row)


def list_stocks(conn: sqlite3.Connection) -> list[StockLevel]:
    rows = conn.execute(
        "SELECT * FROM stocks ORDER BY article_id ASC"
    ).fetchall()
    return [_row(r) for r in rows]


def set_stock(
    conn: sqlite3.Connection, article_id: str, qty_available: float
) -> StockLevel:
    """Definit (ou remplace) le qty_available d'un article (idempotent)."""
    if qty_available < 0:
        raise ValueError("qty_available doit etre positif ou nul")
    # Verifie que l'article existe
    art = conn.execute(
        "SELECT 1 FROM articles WHERE article_id = ?", (article_id,)
    ).fetchone()
    if art is None:
        raise ValueError(f"Article inconnu : {article_id}")

    conn.execute(
        """
        INSERT INTO stocks (article_id, qty_available, qty_reserved, updated_at)
        VALUES (?, ?, 0, datetime('now'))
        ON CONFLICT(article_id) DO UPDATE SET
            qty_available = excluded.qty_available,
            updated_at = datetime('now')
        """,
        (article_id, qty_available),
    )
    return get_stock(conn, article_id)


def reserve(
    conn: sqlite3.Connection, article_id: str, qty: float
) -> StockLevel:
    """Augmente qty_reserved (sans verifier l'inventaire)."""
    if qty <= 0:
        raise ValueError("qty doit etre strictement positif")
    # Garantit la presence de la ligne
    set_stock(conn, article_id, get_stock(conn, article_id).qty_available)
    conn.execute(
        """
        UPDATE stocks
        SET qty_reserved = qty_reserved + ?,
            updated_at = datetime('now')
        WHERE article_id = ?
        """,
        (qty, article_id),
    )
    return get_stock(conn, article_id)


def unreserve(
    conn: sqlite3.Connection, article_id: str, qty: float
) -> StockLevel:
    """Reduit qty_reserved (sans aller sous zero)."""
    if qty <= 0:
        raise ValueError("qty doit etre strictement positif")
    current = get_stock(conn, article_id)
    new_res = max(0.0, current.qty_reserved - qty)
    conn.execute(
        """
        UPDATE stocks
        SET qty_reserved = ?, updated_at = datetime('now')
        WHERE article_id = ?
        """,
        (new_res, article_id),
    )
    return get_stock(conn, article_id)


def project_available(
    conn: sqlite3.Connection, article_id: str
) -> float:
    """Quantite projetee = qty_free + somme des achats ouverts/partiels.

    Utilise par R-P2-05 enrichi en V2.
    """
    stock = get_stock(conn, article_id)
    row = conn.execute(
        """
        SELECT COALESCE(SUM(qty_ordered - qty_received), 0) AS open_qty
        FROM purchase_orders
        WHERE article_id = ? AND status IN ('open', 'partial')
        """,
        (article_id,),
    ).fetchone()
    open_qty = float(row["open_qty"]) if row else 0.0
    return stock.qty_free + open_qty
