"""Routings alternatifs (V2) - implantations paralleles et hybrides.

routing_operations garde la version "principale" du routing (legacy V1).
routing_alternatives permet de declarer des postes additionnels capables
d'executer la meme operation. Lors du choix de poste, on combine les deux
tables et on selectionne par preference + charge courante.

V2 simple : selection deterministe (preference_order ASC, charge ASC en
tie-breaker). Le routage conditionnel (`condition_json`) sera evalue en V3.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class RoutingAlternative:
    alt_id: int
    article_id: str
    sequence_idx: int
    workstation_id: str
    unit_time_min: float
    preference_order: int
    condition_json: str


@dataclass(frozen=True)
class WorkstationChoice:
    workstation_id: str
    unit_time_min: float
    source: str  # 'main' (routing_operations) | 'alt' (routing_alternatives)
    preference_order: int


def add_alternative(
    conn: sqlite3.Connection,
    *,
    article_id: str,
    sequence_idx: int,
    workstation_id: str,
    unit_time_min: float,
    preference_order: int = 100,
    condition_json: str = "{}",
) -> RoutingAlternative:
    if unit_time_min <= 0:
        raise ValueError("unit_time_min doit etre strictement positif")
    art = conn.execute(
        "SELECT 1 FROM articles WHERE article_id = ?", (article_id,)
    ).fetchone()
    if art is None:
        raise ValueError(f"Article inconnu : {article_id}")
    ws = conn.execute(
        "SELECT 1 FROM workstations WHERE workstation_id = ?", (workstation_id,)
    ).fetchone()
    if ws is None:
        raise ValueError(f"Workstation inconnue : {workstation_id}")

    cur = conn.execute(
        """
        INSERT INTO routing_alternatives
            (article_id, sequence_idx, workstation_id, unit_time_min,
             preference_order, condition_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (article_id, sequence_idx, workstation_id, unit_time_min,
         preference_order, condition_json),
    )
    row = conn.execute(
        "SELECT * FROM routing_alternatives WHERE alt_id = ?",
        (cur.lastrowid,),
    ).fetchone()
    return _row(row)


def _row(row: sqlite3.Row) -> RoutingAlternative:
    return RoutingAlternative(
        alt_id=int(row["alt_id"]),
        article_id=row["article_id"],
        sequence_idx=int(row["sequence_idx"]),
        workstation_id=row["workstation_id"],
        unit_time_min=float(row["unit_time_min"]),
        preference_order=int(row["preference_order"]),
        condition_json=row["condition_json"],
    )


def list_alternatives_for(
    conn: sqlite3.Connection, article_id: str, sequence_idx: int
) -> list[RoutingAlternative]:
    rows = conn.execute(
        """
        SELECT * FROM routing_alternatives
        WHERE article_id = ? AND sequence_idx = ?
        ORDER BY preference_order ASC, alt_id ASC
        """,
        (article_id, sequence_idx),
    ).fetchall()
    return [_row(r) for r in rows]


def available_workstations_for(
    conn: sqlite3.Connection, article_id: str, sequence_idx: int
) -> list[WorkstationChoice]:
    """Combine le routing principal + les alternatives pour une (article, seq).

    Le routing principal a preference_order 0 (toujours premier sauf si une
    alternative avec preference_order < 0 est definie).
    """
    out: list[WorkstationChoice] = []
    main = conn.execute(
        """
        SELECT workstation_id, unit_time_min FROM routing_operations
        WHERE article_id = ? AND sequence_idx = ?
        """,
        (article_id, sequence_idx),
    ).fetchone()
    if main is not None:
        out.append(
            WorkstationChoice(
                workstation_id=main["workstation_id"],
                unit_time_min=float(main["unit_time_min"]),
                source="main",
                preference_order=0,
            )
        )
    for alt in list_alternatives_for(conn, article_id, sequence_idx):
        out.append(
            WorkstationChoice(
                workstation_id=alt.workstation_id,
                unit_time_min=alt.unit_time_min,
                source="alt",
                preference_order=alt.preference_order,
            )
        )
    out.sort(key=lambda c: (c.preference_order, c.unit_time_min))
    return out


def pick_workstation(
    conn: sqlite3.Connection,
    article_id: str,
    sequence_idx: int,
    *,
    strategy: str = "preferred",
) -> WorkstationChoice | None:
    """Choisit un poste pour une operation.

    Strategies V2 :
      'preferred' : selon preference_order ASC, puis unit_time_min ASC
      'fastest'   : le poste avec unit_time_min le plus faible
    """
    choices = available_workstations_for(conn, article_id, sequence_idx)
    if not choices:
        return None
    if strategy == "preferred":
        return choices[0]
    if strategy == "fastest":
        return min(choices, key=lambda c: c.unit_time_min)
    raise ValueError(f"strategy inconnue : {strategy!r}")
