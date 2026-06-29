"""Transitions de zones pour les candidate_orders.

Trois zones doctrinales (§6 du cadrage) :
  libre      → après CBN, avant qualification
  négociable → après P2 (qualification)
  gelée      → après P3 (engagement pour exécution)

Sens autorisé : libre → négociable → gelée (en avant)
Sens autorisé : gelée → négociable (P3 inverse forme A, viendra en L1.6)
Tout autre saut est rejeté.

Chaque transition est tracée dans `zone_transitions` (audit immuable).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


ZONE_LIBRE = "libre"
ZONE_NEGOCIABLE = "negociable"
ZONE_GELEE = "gelee"

# Transitions autorisées : from -> {to acceptables}
# La transition retour gelee -> negociable correspond a la forme A de P3 inverse
# qui sera implementee en L1.6.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    ZONE_LIBRE: {ZONE_NEGOCIABLE},
    ZONE_NEGOCIABLE: {ZONE_GELEE, ZONE_LIBRE},
    ZONE_GELEE: {ZONE_NEGOCIABLE},
}


@dataclass(frozen=True)
class ZoneTransition:
    transition_id: int
    subject_type: str
    subject_id: str
    from_zone: str | None
    to_zone: str
    cycle_id: str | None
    decision: str | None
    explanation: str | None
    actor: str | None
    at_time: str


def current_zone(
    conn: sqlite3.Connection, candidate_id: str
) -> str | None:
    """Renvoie la zone courante d'un candidate_order ou None si inconnu."""
    row = conn.execute(
        "SELECT zone FROM candidate_orders WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    return row["zone"] if row else None


def move_candidate_to_zone(
    conn: sqlite3.Connection,
    candidate_id: str,
    target_zone: str,
    *,
    decision: str | None = None,
    rule_ref: str | None = None,
    explanation: str | None = None,
    cycle_id: str | None = None,
    actor: str | None = None,
    event_id: int | None = None,
) -> ZoneTransition:
    """Déplace un candidate_order vers une nouvelle zone.

    Vérifie que la transition est autorisée et la trace dans
    `zone_transitions`. Lève ValueError si transition invalide ou
    candidate inconnu.
    """
    if target_zone not in {ZONE_LIBRE, ZONE_NEGOCIABLE, ZONE_GELEE}:
        raise ValueError(f"Zone cible inconnue : {target_zone!r}")

    from_zone = current_zone(conn, candidate_id)
    if from_zone is None:
        raise ValueError(f"Candidate inconnu : {candidate_id}")

    if from_zone == target_zone:
        raise ValueError(
            f"Candidate {candidate_id} déjà en zone {target_zone!r}"
        )

    allowed = ALLOWED_TRANSITIONS.get(from_zone, set())
    if target_zone not in allowed:
        raise ValueError(
            f"Transition non autorisée : {from_zone!r} -> {target_zone!r} "
            f"(autorisées : {sorted(allowed)})"
        )

    conn.execute(
        "UPDATE candidate_orders SET zone = ? WHERE candidate_id = ?",
        (target_zone, candidate_id),
    )
    cur = conn.execute(
        """
        INSERT INTO zone_transitions
            (subject_type, subject_id, from_zone, to_zone, cycle_id,
             decision, rule_ref, explanation, actor, event_id)
        VALUES ('candidate_order', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (candidate_id, from_zone, target_zone, cycle_id,
         decision, rule_ref, explanation, actor, event_id),
    )
    tid = cur.lastrowid
    assert tid is not None
    return ZoneTransition(
        transition_id=tid,
        subject_type="candidate_order",
        subject_id=candidate_id,
        from_zone=from_zone,
        to_zone=target_zone,
        cycle_id=cycle_id,
        decision=decision,
        explanation=explanation,
        actor=actor,
        at_time="",  # rempli par DB ; non relu ici pour simplicite
    )


def fetch_in_zone(
    conn: sqlite3.Connection, zone: str
) -> list[sqlite3.Row]:
    """Liste les candidate_orders actuellement dans la zone donnee."""
    if zone not in {ZONE_LIBRE, ZONE_NEGOCIABLE, ZONE_GELEE}:
        raise ValueError(f"Zone inconnue : {zone!r}")
    return list(
        conn.execute(
            """
            SELECT candidate_id, sales_order_id, article_id, quantity,
                   status, zone, created_at
            FROM candidate_orders
            WHERE zone = ?
            ORDER BY candidate_id ASC
            """,
            (zone,),
        )
    )


def transitions_for(
    conn: sqlite3.Connection, candidate_id: str
) -> list[ZoneTransition]:
    """Historique chronologique des transitions de zone d'un candidate."""
    rows = conn.execute(
        """
        SELECT transition_id, subject_type, subject_id, from_zone, to_zone,
               cycle_id, decision, explanation, actor, at_time
        FROM zone_transitions
        WHERE subject_type = 'candidate_order' AND subject_id = ?
        ORDER BY transition_id ASC
        """,
        (candidate_id,),
    ).fetchall()
    return [
        ZoneTransition(
            transition_id=int(r["transition_id"]),
            subject_type=r["subject_type"],
            subject_id=r["subject_id"],
            from_zone=r["from_zone"],
            to_zone=r["to_zone"],
            cycle_id=r["cycle_id"],
            decision=r["decision"],
            explanation=r["explanation"],
            actor=r["actor"],
            at_time=r["at_time"],
        )
        for r in rows
    ]
