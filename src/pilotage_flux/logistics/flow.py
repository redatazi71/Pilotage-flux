"""Flux logistique : emplacements, transferts, files."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


KIND_STOCK = "stock"
KIND_WS_IN = "ws_in"
KIND_WS_OUT = "ws_out"
KIND_SHIPPING = "shipping"

EVT_TRANSFER = "transfer"
EVT_FEED = "feed"
EVT_EVACUATE = "evacuate"
EVT_SHIP = "ship"
EVT_RECEIVE = "receive"


@dataclass(frozen=True)
class Location:
    location_id: str
    label: str
    kind: str
    workstation_id: str | None
    capacity: int | None
    created_at: str


@dataclass(frozen=True)
class LogisticEvent:
    log_event_id: int
    of_id: str | None
    of_op_id: int | None
    article_id: str | None
    qty: float
    from_location: str | None
    to_location: str | None
    event_type: str
    explanation: str | None
    actor: str | None
    at_time: str


def _row_location(row: sqlite3.Row) -> Location:
    return Location(
        location_id=row["location_id"],
        label=row["label"],
        kind=row["kind"],
        workstation_id=row["workstation_id"],
        capacity=int(row["capacity"]) if row["capacity"] is not None else None,
        created_at=row["created_at"],
    )


def _row_event(row: sqlite3.Row) -> LogisticEvent:
    return LogisticEvent(
        log_event_id=int(row["log_event_id"]),
        of_id=row["of_id"],
        of_op_id=int(row["of_op_id"]) if row["of_op_id"] is not None else None,
        article_id=row["article_id"],
        qty=float(row["qty"]),
        from_location=row["from_location"],
        to_location=row["to_location"],
        event_type=row["event_type"],
        explanation=row["explanation"],
        actor=row["actor"],
        at_time=row["at_time"],
    )


# -----------------------------------------------------------------------
# Locations
# -----------------------------------------------------------------------

def create_location(
    conn: sqlite3.Connection,
    *,
    location_id: str,
    label: str,
    kind: str,
    workstation_id: str | None = None,
    capacity: int | None = None,
) -> Location:
    if kind not in (KIND_STOCK, KIND_WS_IN, KIND_WS_OUT, KIND_SHIPPING):
        raise ValueError(f"kind inconnu : {kind!r}")
    if workstation_id is not None:
        ws = conn.execute(
            "SELECT 1 FROM workstations WHERE workstation_id = ?",
            (workstation_id,),
        ).fetchone()
        if ws is None:
            raise ValueError(f"workstation inconnue : {workstation_id}")
    conn.execute(
        """
        INSERT INTO locations (location_id, label, kind, workstation_id, capacity)
        VALUES (?, ?, ?, ?, ?)
        """,
        (location_id, label, kind, workstation_id, capacity),
    )
    row = conn.execute(
        "SELECT * FROM locations WHERE location_id = ?", (location_id,)
    ).fetchone()
    return _row_location(row)


def list_locations(
    conn: sqlite3.Connection, *, kind: str | None = None
) -> list[Location]:
    sql = "SELECT * FROM locations WHERE 1=1"
    params: list[str] = []
    if kind is not None:
        sql += " AND kind = ?"
        params.append(kind)
    sql += " ORDER BY location_id ASC"
    return [_row_location(r) for r in conn.execute(sql, params)]


# -----------------------------------------------------------------------
# Events
# -----------------------------------------------------------------------

def _emit(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    of_id: str | None = None,
    of_op_id: int | None = None,
    article_id: str | None = None,
    qty: float = 0.0,
    from_location: str | None = None,
    to_location: str | None = None,
    explanation: str | None = None,
    actor: str | None = None,
) -> LogisticEvent:
    if qty < 0:
        raise ValueError("qty doit etre positif ou nul")
    cur = conn.execute(
        """
        INSERT INTO logistic_events
            (of_id, of_op_id, article_id, qty,
             from_location, to_location, event_type, explanation, actor)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (of_id, of_op_id, article_id, qty,
         from_location, to_location, event_type, explanation, actor),
    )
    row = conn.execute(
        "SELECT * FROM logistic_events WHERE log_event_id = ?",
        (cur.lastrowid,),
    ).fetchone()
    return _row_event(row)


def transfer(
    conn: sqlite3.Connection,
    *,
    article_id: str,
    qty: float,
    from_location: str,
    to_location: str,
    of_id: str | None = None,
    actor: str = "logistics",
) -> LogisticEvent:
    return _emit(
        conn,
        event_type=EVT_TRANSFER, of_id=of_id, article_id=article_id, qty=qty,
        from_location=from_location, to_location=to_location, actor=actor,
    )


def feed_workstation(
    conn: sqlite3.Connection,
    *,
    of_id: str,
    of_op_id: int | None,
    article_id: str,
    qty: float,
    to_location: str,
    actor: str = "logistics",
) -> LogisticEvent:
    """Alimentation d'un poste (feed). to_location attendu = ws_in."""
    return _emit(
        conn,
        event_type=EVT_FEED, of_id=of_id, of_op_id=of_op_id,
        article_id=article_id, qty=qty, to_location=to_location, actor=actor,
    )


def evacuate(
    conn: sqlite3.Connection,
    *,
    of_id: str,
    article_id: str,
    qty: float,
    from_location: str,
    to_location: str | None = None,
    actor: str = "logistics",
) -> LogisticEvent:
    """Evacuation d'un poste apres operation."""
    return _emit(
        conn,
        event_type=EVT_EVACUATE, of_id=of_id, article_id=article_id, qty=qty,
        from_location=from_location, to_location=to_location, actor=actor,
    )


def ship(
    conn: sqlite3.Connection,
    *,
    of_id: str,
    article_id: str,
    qty: float,
    from_location: str,
    actor: str = "logistics",
) -> LogisticEvent:
    """Expedition d'un produit fini."""
    return _emit(
        conn,
        event_type=EVT_SHIP, of_id=of_id, article_id=article_id, qty=qty,
        from_location=from_location, actor=actor,
    )


def receive(
    conn: sqlite3.Connection,
    *,
    article_id: str,
    qty: float,
    to_location: str,
    actor: str = "logistics",
) -> LogisticEvent:
    """Reception physique entrante (souvent suite a un PO)."""
    return _emit(
        conn,
        event_type=EVT_RECEIVE, article_id=article_id, qty=qty,
        to_location=to_location, actor=actor,
    )


def list_events(
    conn: sqlite3.Connection,
    *,
    of_id: str | None = None,
    event_type: str | None = None,
    to_location: str | None = None,
) -> list[LogisticEvent]:
    sql = "SELECT * FROM logistic_events WHERE 1=1"
    params: list[str] = []
    if of_id is not None:
        sql += " AND of_id = ?"
        params.append(of_id)
    if event_type is not None:
        sql += " AND event_type = ?"
        params.append(event_type)
    if to_location is not None:
        sql += " AND to_location = ?"
        params.append(to_location)
    sql += " ORDER BY log_event_id ASC"
    return [_row_event(r) for r in conn.execute(sql, params)]


def queue_at(
    conn: sqlite3.Connection, location_id: str
) -> float:
    """Calcule la file en attente a un emplacement : somme(feed - evacuate)
    pondéré par qty pour les OF passés par cet emplacement."""
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN to_location = ? THEN qty ELSE 0 END), 0)
            - COALESCE(SUM(CASE WHEN from_location = ? THEN qty ELSE 0 END), 0)
            AS net
        FROM logistic_events
        WHERE to_location = ? OR from_location = ?
        """,
        (location_id, location_id, location_id, location_id),
    ).fetchone()
    return float(row["net"]) if row else 0.0
