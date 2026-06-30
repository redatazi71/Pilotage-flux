"""Moteur Delta — décisions appliquées aux déviations (B.1).

Une `delta_decision` matérialise le verdict du moteur Delta sur une
déviation événementielle : quel niveau d'action est retenu (L1..L6),
quel score/fréquence l'a déclenché, quel actor décide, et quelle
issue (executed, rejected, expired).

Wiring : la création d'une décision sur un niveau `requires_human`
peut référencer un `approval_queue_id` (création par le caller —
le wiring est fait en B.3).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pilotage_flux.cybernetic.delta_engine.levels import (
    NIVEAUX_ORDRE,
    get_delta_level,
)


STATUS_PENDING = "pending"
STATUS_EXECUTED = "executed"
STATUS_REJECTED = "rejected"
STATUS_EXPIRED = "expired"
STATUSES = (STATUS_PENDING, STATUS_EXECUTED, STATUS_REJECTED, STATUS_EXPIRED)


@dataclass(frozen=True)
class DeltaDecision:
    delta_decision_id: int
    deviation_id: int | None
    niveau_code: str
    racine_id: str | None
    categorie_code: str | None
    score_magnitude: float | None
    frequency: float | None
    decided_at: str
    executed_at: str | None
    status: str
    approval_queue_id: int | None
    explanation: str | None
    actor: str | None


def create_delta_decision(
    conn: sqlite3.Connection,
    *,
    niveau_code: str,
    decided_at: str,
    deviation_id: int | None = None,
    racine_id: str | None = None,
    categorie_code: str | None = None,
    score_magnitude: float | None = None,
    frequency: float | None = None,
    approval_queue_id: int | None = None,
    explanation: str | None = None,
    actor: str | None = None,
) -> int:
    """Crée une décision Delta.

    Le niveau doit exister (FK), mais le seed peut ne pas être
    réalisé : on vérifie explicitement et on lève ValueError si
    inconnu.
    """
    if niveau_code not in NIVEAUX_ORDRE:
        raise ValueError(
            f"niveau_code invalide : {niveau_code} "
            f"(attendus {NIVEAUX_ORDRE})"
        )
    if get_delta_level(conn, niveau_code) is None:
        raise ValueError(
            f"niveau {niveau_code} pas encore seedé — "
            f"appeler seed_default_delta_levels(conn) d'abord"
        )
    cur = conn.execute(
        """
        INSERT INTO delta_decisions
            (deviation_id, niveau_code, racine_id, categorie_code,
             score_magnitude, frequency, decided_at,
             status, approval_queue_id, explanation, actor)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            deviation_id, niveau_code, racine_id, categorie_code,
            score_magnitude, frequency, decided_at,
            STATUS_PENDING, approval_queue_id, explanation, actor,
        ),
    )
    return int(cur.lastrowid)


def mark_decision_executed(
    conn: sqlite3.Connection,
    delta_decision_id: int,
    *,
    executed_at: str,
    actor: str | None = None,
) -> None:
    """Marque la décision comme exécutée."""
    conn.execute(
        "UPDATE delta_decisions "
        "SET status = ?, executed_at = ?, "
        "    actor = COALESCE(?, actor) "
        "WHERE delta_decision_id = ?",
        (STATUS_EXECUTED, executed_at, actor, delta_decision_id),
    )


def mark_decision_rejected(
    conn: sqlite3.Connection,
    delta_decision_id: int,
    *,
    actor: str | None = None,
    explanation: str | None = None,
) -> None:
    conn.execute(
        "UPDATE delta_decisions "
        "SET status = ?, "
        "    actor = COALESCE(?, actor), "
        "    explanation = COALESCE(?, explanation) "
        "WHERE delta_decision_id = ?",
        (STATUS_REJECTED, actor, explanation, delta_decision_id),
    )


def mark_decision_expired(
    conn: sqlite3.Connection, delta_decision_id: int,
) -> None:
    conn.execute(
        "UPDATE delta_decisions SET status = ? "
        "WHERE delta_decision_id = ?",
        (STATUS_EXPIRED, delta_decision_id),
    )


def get_decision(
    conn: sqlite3.Connection, delta_decision_id: int,
) -> DeltaDecision | None:
    row = conn.execute(
        "SELECT * FROM delta_decisions WHERE delta_decision_id = ?",
        (delta_decision_id,),
    ).fetchone()
    return _row_to_decision(row) if row else None


def list_decisions_for_deviation(
    conn: sqlite3.Connection, deviation_id: int,
) -> list[DeltaDecision]:
    rows = conn.execute(
        "SELECT * FROM delta_decisions WHERE deviation_id = ? "
        "ORDER BY decided_at, delta_decision_id",
        (deviation_id,),
    ).fetchall()
    return [_row_to_decision(r) for r in rows]


def count_decisions_by_level(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
) -> dict[str, int]:
    """Distribution des décisions par niveau (filtre status optionnel).

    Utile pour KPIs nervosité segmentés N1..N4 / L1..L6.
    """
    sql = ("SELECT niveau_code, COUNT(*) AS n FROM delta_decisions ")
    params: list[object] = []
    if status is not None:
        sql += "WHERE status = ? "
        params.append(status)
    sql += "GROUP BY niveau_code"
    rows = conn.execute(sql, params).fetchall()
    return {r["niveau_code"]: int(r["n"]) for r in rows}


def count_decisions_by_cadrage_level(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
) -> dict[int, int]:
    """Distribution agrégée par niveau cadrage v1.3 (N1..N4).

    Permet KPIs nervosité doctrinaux conformes au cadrage de
    référence (1=N1, 2=N2, 3=N3, 4=N4).
    """
    sql = (
        "SELECT lvl.cadrage_level, COUNT(*) AS n "
        "FROM delta_decisions d "
        "JOIN delta_action_levels lvl ON lvl.niveau_code = d.niveau_code "
    )
    params: list[object] = []
    if status is not None:
        sql += "WHERE d.status = ? "
        params.append(status)
    sql += "GROUP BY lvl.cadrage_level"
    rows = conn.execute(sql, params).fetchall()
    return {int(r["cadrage_level"]): int(r["n"]) for r in rows}


def _row_to_decision(row: sqlite3.Row) -> DeltaDecision:
    return DeltaDecision(
        delta_decision_id=int(row["delta_decision_id"]),
        deviation_id=(
            int(row["deviation_id"]) if row["deviation_id"] is not None
            else None
        ),
        niveau_code=row["niveau_code"],
        racine_id=row["racine_id"],
        categorie_code=row["categorie_code"],
        score_magnitude=(
            float(row["score_magnitude"])
            if row["score_magnitude"] is not None else None
        ),
        frequency=(
            float(row["frequency"]) if row["frequency"] is not None
            else None
        ),
        decided_at=row["decided_at"],
        executed_at=row["executed_at"],
        status=row["status"],
        approval_queue_id=(
            int(row["approval_queue_id"])
            if row["approval_queue_id"] is not None else None
        ),
        explanation=row["explanation"],
        actor=row["actor"],
    )
