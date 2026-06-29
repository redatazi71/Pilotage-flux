"""Declarations terrain debut/fin d'operation (V0).

Chaque declaration est enregistree dans `mes_declarations` (granularite
fine) puis aggregee dans `order_operations` (granularite operation). Les
evenements OP_STARTED et OP_FINISHED sont emis dans l'event_store.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pilotage_flux.events import EventType, append_event


@dataclass(frozen=True)
class OperationDeclaration:
    of_op_id: int
    of_id: str
    sequence_idx: int
    workstation_id: str
    kind: str  # 'start' | 'finish'
    event_id: int
    qty_good: float | None = None
    qty_scrap: float | None = None


def _fetch_op(conn: sqlite3.Connection, of_op_id: int) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT of_op_id, of_id, sequence_idx, workstation_id, unit_time_min, status
        FROM order_operations WHERE of_op_id = ?
        """,
        (of_op_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Operation inconnue : of_op_id={of_op_id}")
    return row


def start_operation(
    conn: sqlite3.Connection,
    of_op_id: int,
    *,
    actor: str = "mes.terrain",
    note: str | None = None,
) -> OperationDeclaration:
    """Declare le debut d'une operation : `actual_start` + statut 'running'."""
    op = _fetch_op(conn, of_op_id)
    if op["status"] != "pending":
        raise ValueError(
            f"Operation {of_op_id} en statut {op['status']!r}, attendu 'pending' pour start"
        )

    of_status = conn.execute(
        "SELECT status FROM manufacturing_orders WHERE of_id = ?",
        (op["of_id"],),
    ).fetchone()["status"]
    if of_status not in ("launched", "in_progress"):
        raise ValueError(
            f"OF {op['of_id']} en statut {of_status!r}, doit etre lance pour declarer"
        )

    conn.execute(
        """
        INSERT INTO mes_declarations (of_op_id, kind, at_time, note)
        VALUES (?, 'start', datetime('now'), ?)
        """,
        (of_op_id, note),
    )
    conn.execute(
        """
        UPDATE order_operations
        SET status = 'running', actual_start = datetime('now')
        WHERE of_op_id = ?
        """,
        (of_op_id,),
    )
    # Si c'est la 1ere op a demarrer, passer l'OF a in_progress
    conn.execute(
        """
        UPDATE manufacturing_orders SET status = 'in_progress'
        WHERE of_id = ? AND status = 'launched'
        """,
        (op["of_id"],),
    )

    event_id = append_event(
        conn,
        aggregate_type="manufacturing_order",
        aggregate_id=op["of_id"],
        event_type=EventType.OP_STARTED,
        payload={
            "of_op_id": of_op_id,
            "sequence_idx": int(op["sequence_idx"]),
            "workstation_id": op["workstation_id"],
        },
        actor=actor,
        source_module="mes.declarations",
    )
    return OperationDeclaration(
        of_op_id=of_op_id,
        of_id=op["of_id"],
        sequence_idx=int(op["sequence_idx"]),
        workstation_id=op["workstation_id"],
        kind="start",
        event_id=event_id,
    )


def finish_operation(
    conn: sqlite3.Connection,
    of_op_id: int,
    *,
    qty_good: float,
    qty_scrap: float = 0.0,
    actor: str = "mes.terrain",
    note: str | None = None,
) -> OperationDeclaration:
    """Declare la fin d'une operation : quantites, `actual_end`, statut 'done'."""
    if qty_good < 0 or qty_scrap < 0:
        raise ValueError("Les quantites doivent etre positives ou nulles")

    op = _fetch_op(conn, of_op_id)
    if op["status"] != "running":
        raise ValueError(
            f"Operation {of_op_id} en statut {op['status']!r}, attendu 'running' pour finish"
        )

    conn.execute(
        """
        INSERT INTO mes_declarations
            (of_op_id, kind, at_time, qty_good, qty_scrap, note)
        VALUES (?, 'finish', datetime('now'), ?, ?, ?)
        """,
        (of_op_id, qty_good, qty_scrap, note),
    )
    conn.execute(
        """
        UPDATE order_operations
        SET status = 'done',
            actual_end = datetime('now'),
            qty_good = ?,
            qty_scrap = ?
        WHERE of_op_id = ?
        """,
        (qty_good, qty_scrap, of_op_id),
    )

    event_id = append_event(
        conn,
        aggregate_type="manufacturing_order",
        aggregate_id=op["of_id"],
        event_type=EventType.OP_FINISHED,
        payload={
            "of_op_id": of_op_id,
            "sequence_idx": int(op["sequence_idx"]),
            "workstation_id": op["workstation_id"],
            "qty_good": qty_good,
            "qty_scrap": qty_scrap,
        },
        actor=actor,
        source_module="mes.declarations",
    )
    return OperationDeclaration(
        of_op_id=of_op_id,
        of_id=op["of_id"],
        sequence_idx=int(op["sequence_idx"]),
        workstation_id=op["workstation_id"],
        kind="finish",
        event_id=event_id,
        qty_good=qty_good,
        qty_scrap=qty_scrap,
    )
