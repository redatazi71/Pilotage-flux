"""Aplatissement multi-niveau des nomenclatures (BOM flattening).

Pour chaque article racine fabrique, le parcours recursif de la BOM produit
la liste exhaustive de ses composants (intermediaires et acheres) avec leur
quantite cumulee par unite de racine et leur chemin d'origine (pegging
structurel).

Detecte les cycles et leve ValueError le cas echeant.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class FlatNode:
    root_article: str
    component_article: str
    cumulative_quantity: float
    depth_level: int
    is_leaf: bool
    path: str


def _children_of(conn: sqlite3.Connection, article_id: str) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT child_article, quantity FROM bom_lines WHERE parent_article = ?",
            (article_id,),
        )
    )


def _is_purchased(conn: sqlite3.Connection, article_id: str) -> bool:
    row = conn.execute(
        "SELECT is_purchased FROM articles WHERE article_id = ?", (article_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Article inconnu : {article_id}")
    return bool(row["is_purchased"])


def _flatten_recursive(
    conn: sqlite3.Connection,
    root: str,
    current: str,
    qty: float,
    depth: int,
    path: str,
    visited: set[str],
    out: list[FlatNode],
) -> None:
    if current in visited:
        raise ValueError(
            f"Cycle BOM detecte sur {current!r} (chemin {path})"
        )
    visited = visited | {current}

    children = _children_of(conn, current)
    is_purchased = _is_purchased(conn, current)
    is_leaf_here = is_purchased or not children

    # Le noeud racine lui-meme n'est pas insere ; on s'interesse a ses descendants.
    if depth > 0:
        out.append(
            FlatNode(
                root_article=root,
                component_article=current,
                cumulative_quantity=qty,
                depth_level=depth,
                is_leaf=is_leaf_here,
                path=path,
            )
        )

    if is_purchased:
        return
    for child in children:
        child_id = child["child_article"]
        _flatten_recursive(
            conn=conn,
            root=root,
            current=child_id,
            qty=qty * float(child["quantity"]),
            depth=depth + 1,
            path=f"{path}/{child_id}",
            visited=visited,
            out=out,
        )


def flatten_bom_for_article(
    conn: sqlite3.Connection, root_article: str
) -> list[FlatNode]:
    """Renvoie l'aplatissement complet de la BOM d'un article racine.

    Le noeud racine n'apparait pas dans la sortie ; seuls ses descendants
    (intermediaires fabriques et composants acheres) sont retournes.
    """
    out: list[FlatNode] = []
    _flatten_recursive(
        conn=conn,
        root=root_article,
        current=root_article,
        qty=1.0,
        depth=0,
        path=f"/{root_article}",
        visited=set(),
        out=out,
    )
    return out


def persist_flattened_bom(conn: sqlite3.Connection) -> int:
    """Aplatit toutes les BOM des articles fabriques et persiste le resultat.

    Renvoie le nombre de lignes inserees. Idempotent : si la table est deja
    peuplee pour un article, ses lignes sont remplacees.
    """
    roots = conn.execute(
        "SELECT article_id FROM articles WHERE is_purchased = 0 ORDER BY article_id"
    ).fetchall()
    n_inserted = 0
    for r in roots:
        nodes = flatten_bom_for_article(conn, r["article_id"])
        if not nodes:
            continue
        conn.execute(
            "DELETE FROM flattened_bom_lines WHERE root_article = ?",
            (r["article_id"],),
        )
        for n in nodes:
            conn.execute(
                """
                INSERT INTO flattened_bom_lines
                    (root_article, component_article, cumulative_quantity,
                     depth_level, is_leaf, path)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    n.root_article,
                    n.component_article,
                    n.cumulative_quantity,
                    n.depth_level,
                    1 if n.is_leaf else 0,
                    n.path,
                ),
            )
            n_inserted += 1
    return n_inserted


def get_manufactured_components(
    conn: sqlite3.Connection, root_article: str
) -> list[FlatNode]:
    """Renvoie uniquement les composants intermediaires fabriques (non-feuilles).

    Utile pour le CBN multi-niveau qui doit creer un candidate_order par
    article fabrique du tree.
    """
    return [
        n
        for n in flatten_bom_for_article(conn, root_article)
        if not n.is_leaf
    ]


def get_purchased_components(
    conn: sqlite3.Connection, root_article: str
) -> list[FlatNode]:
    """Renvoie uniquement les composants achetes (feuilles)."""
    return [
        n for n in flatten_bom_for_article(conn, root_article) if n.is_leaf
    ]
