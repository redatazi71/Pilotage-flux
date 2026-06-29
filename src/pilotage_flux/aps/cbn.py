"""Calcul des besoins nets (CBN) mono-niveau, V0.

Pour chaque sales_order ouverte, cree un candidate_order par article fini.
Le pegging mono-niveau est conserve dans candidate_orders.sales_order_id.

V0 simplification : on ne fabrique que l'article du sales_order (pas de
multi-niveau, pas de recursion sur les composants achetes). Les composants
sont reputes achetes (`articles.is_purchased = 1`).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class NetRequirement:
    candidate_id: str
    sales_order_id: str
    article_id: str
    quantity: float


def _next_candidate_id(conn: sqlite3.Connection) -> str:
    """Genere un identifiant lisible CND-NNNN incremental."""
    row = conn.execute(
        """
        SELECT candidate_id FROM candidate_orders
        ORDER BY candidate_id DESC LIMIT 1
        """
    ).fetchone()
    if row is None:
        return "CND-0001"
    last = row["candidate_id"]
    try:
        n = int(last.split("-")[-1])
    except (ValueError, IndexError):
        n = 0
    return f"CND-{n + 1:04d}"


def compute_candidates(conn: sqlite3.Connection) -> list[NetRequirement]:
    """Cree un candidate_order par sales_order ouverte sans candidat associe.

    Renvoie la liste des besoins nets generes pendant cet appel.
    Idempotent : un sales_order deja relie a un candidat ne genere rien.
    """
    rows = conn.execute(
        """
        SELECT so.sales_order_id, so.article_id, so.quantity
        FROM sales_orders AS so
        LEFT JOIN candidate_orders AS co
            ON co.sales_order_id = so.sales_order_id
        WHERE so.status = 'open'
          AND co.candidate_id IS NULL
        ORDER BY so.due_date ASC, so.sales_order_id ASC
        """
    ).fetchall()

    created: list[NetRequirement] = []
    for row in rows:
        cid = _next_candidate_id(conn)
        conn.execute(
            """
            INSERT INTO candidate_orders
                (candidate_id, sales_order_id, article_id, quantity, status)
            VALUES (?, ?, ?, ?, 'candidate')
            """,
            (cid, row["sales_order_id"], row["article_id"], row["quantity"]),
        )
        created.append(
            NetRequirement(
                candidate_id=cid,
                sales_order_id=row["sales_order_id"],
                article_id=row["article_id"],
                quantity=row["quantity"],
            )
        )
    return created
