"""Porte P1 - Entree en zone libre (V0).

V0 simplification : la porte P1 promeut systematiquement les candidate_orders
en manufacturing_orders, sans criteres de filtrage. La capacite est evaluee
pour information ; un depassement de capacite genere un evenement de log mais
ne bloque pas la creation (V0 OF-driven).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pilotage_flux.aps.cbn import compute_candidates, NetRequirement
from pilotage_flux.aps.capacity import compute_load_by_workstation, WorkstationLoad
from pilotage_flux.aps.planner import promote_candidate_to_of, PlanningResult


@dataclass
class P1Outcome:
    candidates_created: list[NetRequirement]
    ofs_created: list[PlanningResult]
    workstation_load: list[WorkstationLoad]

    @property
    def has_overload(self) -> bool:
        return any(w.is_overloaded for w in self.workstation_load)


def run_p1_promotion(conn: sqlite3.Connection, *, actor: str = "gate.p1") -> P1Outcome:
    """Execute la porte P1 sur l'ensemble des sales_orders ouverts.

    1. CBN -> candidate_orders pour les SO sans candidat
    2. Charge/capacite informative
    3. Promotion candidate -> OF pour chaque candidate non encore promu
    """
    conn.execute("BEGIN")
    try:
        candidates = compute_candidates(conn)

        all_pending = conn.execute(
            "SELECT candidate_id FROM candidate_orders WHERE status = 'candidate'"
        ).fetchall()
        pending_ids = [row["candidate_id"] for row in all_pending]

        load = compute_load_by_workstation(conn, candidate_ids=pending_ids)

        promotions = [
            promote_candidate_to_of(conn, cid, actor=actor)
            for cid in pending_ids
        ]
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return P1Outcome(
        candidates_created=candidates,
        ofs_created=promotions,
        workstation_load=load,
    )
