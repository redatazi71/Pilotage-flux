"""Pegging multi-niveau : trace demande -> candidate -> OF -> composant.

Chaque lien est unidirectionnel (source -> target). La chaine de pegging
est reconstituee par parcours transitif (recursive CTE-like en Python).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class PeggingLink:
    pegging_id: int
    source_type: str
    source_id: str
    target_type: str
    target_id: str
    article_id: str | None
    quantity: float
    depth: int


def add_pegging_link(
    conn: sqlite3.Connection,
    *,
    source_type: str,
    source_id: str,
    target_type: str,
    target_id: str,
    quantity: float,
    article_id: str | None = None,
    depth: int = 0,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO pegging_links
            (source_type, source_id, target_type, target_id,
             article_id, quantity, depth)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (source_type, source_id, target_type, target_id,
         article_id, quantity, depth),
    )
    pid = cur.lastrowid
    assert pid is not None
    return pid


def _row_to_link(row: sqlite3.Row) -> PeggingLink:
    return PeggingLink(
        pegging_id=int(row["pegging_id"]),
        source_type=row["source_type"],
        source_id=row["source_id"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        article_id=row["article_id"],
        quantity=float(row["quantity"]),
        depth=int(row["depth"]),
    )


def get_outgoing(
    conn: sqlite3.Connection, source_type: str, source_id: str
) -> list[PeggingLink]:
    """Liens partant d'une source donnee (descendants directs)."""
    rows = conn.execute(
        """
        SELECT * FROM pegging_links
        WHERE source_type = ? AND source_id = ?
        ORDER BY pegging_id ASC
        """,
        (source_type, source_id),
    ).fetchall()
    return [_row_to_link(r) for r in rows]


def get_incoming(
    conn: sqlite3.Connection, target_type: str, target_id: str
) -> list[PeggingLink]:
    """Liens arrivant sur une cible (ascendants directs)."""
    rows = conn.execute(
        """
        SELECT * FROM pegging_links
        WHERE target_type = ? AND target_id = ?
        ORDER BY pegging_id ASC
        """,
        (target_type, target_id),
    ).fetchall()
    return [_row_to_link(r) for r in rows]


def get_pegging_chain(
    conn: sqlite3.Connection, source_type: str, source_id: str
) -> list[PeggingLink]:
    """Parcours transitif (descendants) depuis une source.

    Renvoie tous les liens atteignables (BFS), tries par profondeur croissante.
    Utilise pour repondre a "tous les OF generes par la demande SO-001".
    """
    seen: set[tuple[str, str]] = {(source_type, source_id)}
    queue: list[tuple[str, str]] = [(source_type, source_id)]
    chain: list[PeggingLink] = []
    while queue:
        s_type, s_id = queue.pop(0)
        for link in get_outgoing(conn, s_type, s_id):
            chain.append(link)
            key = (link.target_type, link.target_id)
            if key not in seen:
                seen.add(key)
                queue.append(key)
    chain.sort(key=lambda l: (l.depth, l.pegging_id))
    return chain


def get_root_demand(
    conn: sqlite3.Connection, target_type: str, target_id: str
) -> tuple[str, str] | None:
    """Remonte la chaine de pegging jusqu'a la demande d'origine (sales_order).

    Renvoie (source_type, source_id) de la racine ou None si introuvable.
    """
    current: tuple[str, str] = (target_type, target_id)
    seen: set[tuple[str, str]] = {current}
    while True:
        incoming = get_incoming(conn, *current)
        if not incoming:
            # Plus de parent ; le noeud courant est racine si c'est un sales_order
            if current[0] == "sales_order":
                return current
            return None
        # On suit le premier parent (V1 : pas de demande multiplexee)
        parent = (incoming[0].source_type, incoming[0].source_id)
        if parent in seen:
            return None  # Cycle, ne devrait pas arriver
        seen.add(parent)
        current = parent
        if current[0] == "sales_order":
            return current
