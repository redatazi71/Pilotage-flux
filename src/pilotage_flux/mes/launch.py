"""Lancement d'un OF en execution (V0).

Le MES recoit un OF en statut 'created' et le passe en 'launched'. Cela
correspond, dans la doctrine V0, a l'engagement du MES sur l'execution.
Un evenement OF_LAUNCHED est emis dans l'event_store.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pilotage_flux.events import EventType, append_event


@dataclass(frozen=True)
class LaunchResult:
    of_id: str
    event_id: int


def launch_of(
    conn: sqlite3.Connection, of_id: str, *, actor: str = "mes.launch"
) -> LaunchResult:
    """Passe l'OF de 'created' a 'launched' et trace l'evenement."""
    row = conn.execute(
        "SELECT of_id, status FROM manufacturing_orders WHERE of_id = ?", (of_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"OF inconnu : {of_id}")
    if row["status"] != "created":
        raise ValueError(
            f"OF {of_id} en statut {row['status']!r}, attendu 'created' pour le lancement"
        )

    conn.execute(
        """
        UPDATE manufacturing_orders
        SET status = 'launched', actual_start = datetime('now')
        WHERE of_id = ?
        """,
        (of_id,),
    )
    event_id = append_event(
        conn,
        aggregate_type="manufacturing_order",
        aggregate_id=of_id,
        event_type=EventType.OF_LAUNCHED,
        payload={"previous_status": "created", "new_status": "launched"},
        actor=actor,
        source_module="mes.launch",
    )
    return LaunchResult(of_id=of_id, event_id=event_id)
