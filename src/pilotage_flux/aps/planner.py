"""Promotion d'un candidate_order vers un manufacturing_order (OF) V0.

Cree l'OF, ses operations planifiees a partir du routing, puis trace la
decision a la porte P1 et emet un evenement OF_CREATED.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pilotage_flux.events import EventType, append_event


@dataclass(frozen=True)
class PlanningResult:
    of_id: str
    candidate_id: str
    article_id: str
    quantity: float
    operations: int
    event_id: int


def _next_of_id(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT of_id FROM manufacturing_orders ORDER BY of_id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return "OF-0001"
    last = row["of_id"]
    try:
        n = int(last.split("-")[-1])
    except (ValueError, IndexError):
        n = 0
    return f"OF-{n + 1:04d}"


def promote_candidate_to_of(
    conn: sqlite3.Connection,
    candidate_id: str,
    *,
    actor: str = "aps.planner",
) -> PlanningResult:
    """Cree un manufacturing_order a partir d'un candidate_order.

    Etapes :
      1. Lecture du candidat (article, quantite, sales_order)
      2. Creation de l'OF avec statut 'created'
      3. Creation des order_operations a partir des routing_operations
      4. Trace decision P1 + evenement OF_CREATED
      5. Mise a jour du statut candidat -> 'promoted'
    """
    cand = conn.execute(
        """
        SELECT candidate_id, sales_order_id, article_id, quantity, status
        FROM candidate_orders WHERE candidate_id = ?
        """,
        (candidate_id,),
    ).fetchone()
    if cand is None:
        raise ValueError(f"Candidate inconnu : {candidate_id}")
    if cand["status"] != "candidate":
        raise ValueError(
            f"Candidate {candidate_id} en statut {cand['status']!r}, attendu 'candidate'"
        )

    of_id = _next_of_id(conn)
    quantity = float(cand["quantity"])

    conn.execute(
        """
        INSERT INTO manufacturing_orders
            (of_id, candidate_id, article_id, quantity, status)
        VALUES (?, ?, ?, ?, 'created')
        """,
        (of_id, candidate_id, cand["article_id"], quantity),
    )

    routing = conn.execute(
        """
        SELECT sequence_idx, workstation_id, unit_time_min
        FROM routing_operations
        WHERE article_id = ?
        ORDER BY sequence_idx ASC
        """,
        (cand["article_id"],),
    ).fetchall()

    if not routing:
        raise ValueError(
            f"Aucune gamme definie pour l'article {cand['article_id']}"
        )

    for op in routing:
        conn.execute(
            """
            INSERT INTO order_operations
                (of_id, sequence_idx, workstation_id, unit_time_min, status)
            VALUES (?, ?, ?, ?, 'pending')
            """,
            (
                of_id,
                int(op["sequence_idx"]),
                op["workstation_id"],
                float(op["unit_time_min"]),
            ),
        )

    event_id = append_event(
        conn,
        aggregate_type="manufacturing_order",
        aggregate_id=of_id,
        event_type=EventType.OF_CREATED,
        payload={
            "candidate_id": candidate_id,
            "sales_order_id": cand["sales_order_id"],
            "article_id": cand["article_id"],
            "quantity": quantity,
            "operations": len(routing),
        },
        actor=actor,
        source_module="aps.planner",
    )

    conn.execute(
        """
        INSERT INTO gate_decisions
            (gate, subject_type, subject_id, decision, rule_ref, explanation, event_id)
        VALUES ('P1', 'manufacturing_order', ?, 'CREATE',
                'P1.create_from_candidate',
                'Promotion automatique du candidate_order en OF (V0 mono-niveau).',
                ?)
        """,
        (of_id, event_id),
    )

    conn.execute(
        "UPDATE candidate_orders SET status = 'promoted' WHERE candidate_id = ?",
        (candidate_id,),
    )

    return PlanningResult(
        of_id=of_id,
        candidate_id=candidate_id,
        article_id=cand["article_id"],
        quantity=quantity,
        operations=len(routing),
        event_id=event_id,
    )
