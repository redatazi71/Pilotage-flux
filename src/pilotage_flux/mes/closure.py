"""Cloture d'un OF (porte P4) - V0.

Conditions :
  - Toutes les operations sont en statut 'done'
  - L'OF est en statut 'in_progress' (ou 'launched' si aucune op n'a tourne)

Resultat :
  - Aggregation des quantites au niveau OF (qty_good / qty_scrap = derniere op)
  - Statut OF -> 'closed'
  - Evenement OF_CLOSED dans l'event_store
  - Decision P4 dans gate_decisions
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pilotage_flux.events import EventType, append_event


@dataclass(frozen=True)
class CloseResult:
    of_id: str
    qty_good: float
    qty_scrap: float
    event_id: int


def close_of(
    conn: sqlite3.Connection, of_id: str, *, actor: str = "mes.closure"
) -> CloseResult:
    of = conn.execute(
        "SELECT of_id, status, quantity FROM manufacturing_orders WHERE of_id = ?",
        (of_id,),
    ).fetchone()
    if of is None:
        raise ValueError(f"OF inconnu : {of_id}")
    if of["status"] not in ("in_progress", "launched"):
        raise ValueError(
            f"OF {of_id} en statut {of['status']!r}, attendu 'in_progress' ou 'launched' pour cloture"
        )

    ops = conn.execute(
        """
        SELECT sequence_idx, status, qty_good, qty_scrap
        FROM order_operations
        WHERE of_id = ?
        ORDER BY sequence_idx ASC
        """,
        (of_id,),
    ).fetchall()
    if not ops:
        raise ValueError(f"OF {of_id} sans operations")
    not_done = [o for o in ops if o["status"] != "done"]
    if not_done:
        raise ValueError(
            f"OF {of_id} : {len(not_done)} operation(s) non terminee(s)"
        )

    last_op = ops[-1]
    qty_good = float(last_op["qty_good"] or 0.0)
    qty_scrap = float(last_op["qty_scrap"] or 0.0)

    conn.execute(
        """
        UPDATE manufacturing_orders
        SET status = 'closed',
            actual_end = datetime('now'),
            qty_good = ?,
            qty_scrap = ?
        WHERE of_id = ?
        """,
        (qty_good, qty_scrap, of_id),
    )

    event_id = append_event(
        conn,
        aggregate_type="manufacturing_order",
        aggregate_id=of_id,
        event_type=EventType.OF_CLOSED,
        payload={
            "qty_good": qty_good,
            "qty_scrap": qty_scrap,
            "operations": len(ops),
            "yield_rate": qty_good / float(of["quantity"]) if of["quantity"] else None,
        },
        actor=actor,
        source_module="mes.closure",
    )

    conn.execute(
        """
        INSERT INTO gate_decisions
            (gate, subject_type, subject_id, decision, rule_ref, explanation, event_id)
        VALUES ('P4', 'manufacturing_order', ?, 'CLOSE',
                'P4.close_after_all_ops_done',
                'Cloture apres terminaison de toutes les operations (V0).',
                ?)
        """,
        (of_id, event_id),
    )

    return CloseResult(of_id=of_id, qty_good=qty_good, qty_scrap=qty_scrap, event_id=event_id)
