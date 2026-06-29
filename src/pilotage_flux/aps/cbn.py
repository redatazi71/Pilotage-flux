"""Calcul des besoins nets (CBN) multi-niveau, V1.

Pour chaque sales_order ouverte, cree :
  1. Un candidate_order pour l'article fini de la commande
  2. Pour chaque article fabrique intermediaire de la BOM (non-feuille),
     un candidate_order avec la quantite cumulee
  3. Les pegging_links qui tracent demande -> candidates -> composants

Composants achetes (`is_purchased = 1`) : ne genrent pas de candidate
mais sont traces en pegging_links comme `target_type='component'`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pilotage_flux.aps.bom_flattener import flatten_bom_for_article
from pilotage_flux.aps.pegging import add_pegging_link


@dataclass(frozen=True)
class NetRequirement:
    candidate_id: str
    sales_order_id: str
    article_id: str
    quantity: float
    depth_level: int  # 0 = article fini, 1+ = sub-niveau


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


def _insert_candidate(
    conn: sqlite3.Connection,
    *,
    sales_order_id: str,
    article_id: str,
    quantity: float,
) -> str:
    cid = _next_candidate_id(conn)
    conn.execute(
        """
        INSERT INTO candidate_orders
            (candidate_id, sales_order_id, article_id, quantity, status)
        VALUES (?, ?, ?, ?, 'candidate')
        """,
        (cid, sales_order_id, article_id, quantity),
    )
    return cid


def compute_candidates(conn: sqlite3.Connection) -> list[NetRequirement]:
    """Cree candidate_orders + pegging_links pour chaque sales_order ouvert.

    Multi-niveau : pour un SO d'un article ART-A dont la BOM contient SEMI-1
    (fabrique) et COMP-X (achete), on cree 2 candidates (ART-A et SEMI-1) et
    on trace les liens SO -> CND(ART-A) -> CND(SEMI-1) -> composants achetes.

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
        so_id = row["sales_order_id"]
        finished_article = row["article_id"]
        finished_qty = float(row["quantity"])

        # 1. Candidate pour l'article fini
        finished_cid = _insert_candidate(
            conn,
            sales_order_id=so_id,
            article_id=finished_article,
            quantity=finished_qty,
        )
        created.append(
            NetRequirement(
                candidate_id=finished_cid,
                sales_order_id=so_id,
                article_id=finished_article,
                quantity=finished_qty,
                depth_level=0,
            )
        )
        add_pegging_link(
            conn,
            source_type="sales_order",
            source_id=so_id,
            target_type="candidate_order",
            target_id=finished_cid,
            article_id=finished_article,
            quantity=finished_qty,
            depth=0,
        )

        # 2. Aplatissement BOM : un candidate par article fabrique intermediaire
        #    et un pegging_link par composant achete
        flat_nodes = flatten_bom_for_article(conn, finished_article)
        # Map article -> candidate_id pour relier les sous-candidates a leur parent direct.
        # En V1 mono-parent (BOM en arbre), on utilise le parent du path.
        article_to_cand: dict[str, str] = {finished_article: finished_cid}

        for node in flat_nodes:
            required_qty = node.cumulative_quantity * finished_qty
            # Parent direct = avant-dernier element du path
            parts = node.path.strip("/").split("/")
            parent_article = parts[-2] if len(parts) >= 2 else finished_article
            parent_cand = article_to_cand.get(parent_article)

            if node.is_leaf:
                # Composant achete : pas de candidate, juste un pegging_link
                add_pegging_link(
                    conn,
                    source_type="candidate_order",
                    source_id=parent_cand or finished_cid,
                    target_type="component",
                    target_id=node.component_article,
                    article_id=node.component_article,
                    quantity=required_qty,
                    depth=node.depth_level,
                )
            else:
                sub_cid = _insert_candidate(
                    conn,
                    sales_order_id=so_id,
                    article_id=node.component_article,
                    quantity=required_qty,
                )
                created.append(
                    NetRequirement(
                        candidate_id=sub_cid,
                        sales_order_id=so_id,
                        article_id=node.component_article,
                        quantity=required_qty,
                        depth_level=node.depth_level,
                    )
                )
                add_pegging_link(
                    conn,
                    source_type="candidate_order",
                    source_id=parent_cand or finished_cid,
                    target_type="candidate_order",
                    target_id=sub_cid,
                    article_id=node.component_article,
                    quantity=required_qty,
                    depth=node.depth_level,
                )
                article_to_cand[node.component_article] = sub_cid

    return created
