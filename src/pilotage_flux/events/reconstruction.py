"""Reconstruction de l'etat d'un OF a partir de l'event_store seul.

Demontre que la trajectoire complete est rejouable depuis les evenements.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from pilotage_flux.events.event_store import fetch_events_for, parse_payload


@dataclass
class ReconstructedOF:
    of_id: str
    status: str = "unknown"
    article_id: str | None = None
    quantity: float | None = None
    qty_good: float = 0.0
    qty_scrap: float = 0.0
    operations_started: list[int] = field(default_factory=list)
    operations_finished: list[int] = field(default_factory=list)
    event_count: int = 0
    timeline: list[str] = field(default_factory=list)


def reconstruct_of(conn: sqlite3.Connection, of_id: str) -> ReconstructedOF:
    """Rejoue les evenements d'un OF et renvoie l'etat reconstruit.

    Aucune lecture des tables `manufacturing_orders` / `order_operations` :
    la seule source de verite est `event_store`.
    """
    events = fetch_events_for(conn, "manufacturing_order", of_id)
    state = ReconstructedOF(of_id=of_id, event_count=len(events))

    for ev in events:
        payload = parse_payload(ev)
        et = ev["event_type"]
        state.timeline.append(f"{ev['occurred_at']} {et}")

        if et == "OF_CREATED":
            state.status = "created"
            state.article_id = payload.get("article_id")
            state.quantity = payload.get("quantity")
        elif et == "OF_LAUNCHED":
            state.status = "launched"
        elif et == "OP_STARTED":
            state.status = "in_progress"
            op_id = payload.get("of_op_id")
            if op_id is not None:
                state.operations_started.append(int(op_id))
        elif et == "OP_FINISHED":
            op_id = payload.get("of_op_id")
            if op_id is not None:
                state.operations_finished.append(int(op_id))
            # Aggregation derniere fin = quantite courante de l'OF
            if "qty_good" in payload:
                state.qty_good = float(payload["qty_good"])
            if "qty_scrap" in payload:
                state.qty_scrap = float(payload["qty_scrap"])
        elif et == "OF_CLOSED":
            state.status = "closed"
            if "qty_good" in payload:
                state.qty_good = float(payload["qty_good"])
            if "qty_scrap" in payload:
                state.qty_scrap = float(payload["qty_scrap"])

    return state
