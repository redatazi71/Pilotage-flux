"""Event store append-only + lectures.

L'event store est la source de verite pour la reconstruction de l'etat
d'un agregat (OF, contrat, etc.). Aucune modification d'evenement n'est
permise apres ecriture - seul un nouvel evenement peut corriger un etat.
"""

from __future__ import annotations

import json
import sqlite3
from enum import Enum
from typing import Any


class EventType(str, Enum):
    """Types d'evenements V0. Etendus dans V1+."""

    OF_CREATED = "OF_CREATED"
    OF_LAUNCHED = "OF_LAUNCHED"
    OP_STARTED = "OP_STARTED"
    OP_FINISHED = "OP_FINISHED"
    OF_CLOSED = "OF_CLOSED"
    GATE_DECISION = "GATE_DECISION"


def append_event(
    conn: sqlite3.Connection,
    *,
    aggregate_type: str,
    aggregate_id: str,
    event_type: EventType | str,
    payload: dict[str, Any] | None = None,
    actor: str | None = None,
    source_module: str | None = None,
) -> int:
    """Ajoute un evenement et renvoie son event_id."""
    if isinstance(event_type, EventType):
        event_type_str = event_type.value
    elif isinstance(event_type, str):
        event_type_str = event_type
    else:
        raise TypeError("event_type must be str or EventType")
    payload_json = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
    cur = conn.execute(
        """
        INSERT INTO event_store
            (aggregate_type, aggregate_id, event_type, payload_json, actor, source_module)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (aggregate_type, aggregate_id, event_type_str, payload_json, actor, source_module),
    )
    event_id = cur.lastrowid
    assert event_id is not None
    return event_id


def fetch_events(conn: sqlite3.Connection, *, limit: int | None = None) -> list[sqlite3.Row]:
    """Toutes les evenements, par ordre chronologique."""
    sql = "SELECT * FROM event_store ORDER BY event_id ASC"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return list(conn.execute(sql))


def fetch_events_for(
    conn: sqlite3.Connection,
    aggregate_type: str,
    aggregate_id: str,
) -> list[sqlite3.Row]:
    """Evenements d'un agregat donne, par ordre chronologique.

    Utilise pour la reconstruction d'etat.
    """
    return list(
        conn.execute(
            """
            SELECT * FROM event_store
            WHERE aggregate_type = ? AND aggregate_id = ?
            ORDER BY event_id ASC
            """,
            (aggregate_type, aggregate_id),
        )
    )


def parse_payload(row: sqlite3.Row) -> dict[str, Any]:
    """Decode le payload JSON d'un row event_store."""
    raw = row["payload_json"]
    if not raw:
        return {}
    return json.loads(raw)
