"""V12.3 — Queue d'approbation pour les niveaux L3/L4.

Toute décision classifiée L3 ou L4 par `dispatcher.dispatch_decision`
y est enqueue en statut 'pending' jusqu'à approbation humaine
(ou auto_timeout en simulation).

Les opérations CRUD sont strictement transactionnelles et tracent
l'identité du décideur (humain ou simulation).
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from pilotage_flux.cybernetic.delta_engine.autonomy_levels import (
    AUTONOMY_LEVEL_L3,
    AUTONOMY_LEVEL_L4,
    REQUIRES_APPROVAL,
)


STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_AUTO_TIMEOUT = "auto_timeout"


@dataclass(frozen=True)
class ApprovalEntry:
    queue_id: int
    decision_id: int
    autonomy_level: str
    status: str
    submitted_at: str
    approved_at: str | None
    approved_by: str | None
    approval_lag_min: float | None
    notes: str | None


def submit_to_approval_queue(
    conn: sqlite3.Connection,
    decision_id: int,
    autonomy_level: str,
    *,
    notes: str | None = None,
) -> int:
    """Enqueue une décision L3 ou L4 pour validation humaine.

    Returns
    -------
    queue_id : int
        L'identifiant de l'entrée créée.

    Raises
    ------
    ValueError
        Si le niveau n'est pas dans REQUIRES_APPROVAL (L1/L2 = autonomes,
        ne doivent jamais passer ici).
    """
    if autonomy_level not in REQUIRES_APPROVAL:
        raise ValueError(
            f"Niveau {autonomy_level!r} ne requiert pas d'approbation "
            f"(seuls L3 et L4 utilisent la queue)"
        )
    cur = conn.execute(
        """
        INSERT INTO approval_queue (decision_id, autonomy_level, notes)
        VALUES (?, ?, ?)
        """,
        (decision_id, autonomy_level, notes),
    )
    return int(cur.lastrowid)


def approve_decision(
    conn: sqlite3.Connection,
    queue_id: int,
    approved_by: str,
    *,
    notes: str | None = None,
) -> ApprovalEntry:
    """Approuve une décision en attente. `approved_by` est tracé."""
    row = conn.execute(
        "SELECT submitted_at, status FROM approval_queue WHERE queue_id = ?",
        (queue_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Queue ID inconnu : {queue_id}")
    if row["status"] != STATUS_PENDING:
        raise ValueError(
            f"Queue ID {queue_id} déjà traité (statut: {row['status']})"
        )

    submitted = datetime.fromisoformat(row["submitted_at"])
    now = datetime.utcnow()
    lag_min = (now - submitted).total_seconds() / 60.0

    conn.execute(
        """
        UPDATE approval_queue SET
            status = ?, approved_at = ?, approved_by = ?,
            approval_lag_min = ?, notes = COALESCE(?, notes)
        WHERE queue_id = ?
        """,
        (STATUS_APPROVED, now.isoformat(), approved_by,
         lag_min, notes, queue_id),
    )
    return _fetch_entry(conn, queue_id)


def reject_decision(
    conn: sqlite3.Connection,
    queue_id: int,
    rejected_by: str,
    *,
    notes: str | None = None,
) -> ApprovalEntry:
    """Rejette une décision en attente. La décision n'est PAS appliquée."""
    row = conn.execute(
        "SELECT submitted_at, status FROM approval_queue WHERE queue_id = ?",
        (queue_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Queue ID inconnu : {queue_id}")
    if row["status"] != STATUS_PENDING:
        raise ValueError(
            f"Queue ID {queue_id} déjà traité (statut: {row['status']})"
        )

    submitted = datetime.fromisoformat(row["submitted_at"])
    now = datetime.utcnow()
    lag_min = (now - submitted).total_seconds() / 60.0

    conn.execute(
        """
        UPDATE approval_queue SET
            status = ?, approved_at = ?, approved_by = ?,
            approval_lag_min = ?, notes = COALESCE(?, notes)
        WHERE queue_id = ?
        """,
        (STATUS_REJECTED, now.isoformat(), rejected_by,
         lag_min, notes, queue_id),
    )
    return _fetch_entry(conn, queue_id)


def auto_approve_with_lag(
    conn: sqlite3.Connection,
    queue_id: int,
    *,
    mean_lag_minutes: float = 240.0,
    std_lag_minutes: float = 60.0,
    rng: random.Random | None = None,
) -> ApprovalEntry:
    """Approuve automatiquement avec lag normal (simulation).

    Modèle de validation humaine simulée :
      - L3 : lag moyen 4 h (240 min), σ = 1 h (60 min) — décision opérateur
      - L4 : lag moyen 8 h (480 min), σ = 2 h (120 min) — décision supervisor

    Le lag est mesuré comme temps réel écoulé (en minutes) entre
    `submitted_at` et `approved_at`. En simulation, on enregistre un
    lag fictif en ajustant `approval_lag_min` sans toucher au temps
    système.
    """
    row = conn.execute(
        "SELECT submitted_at, autonomy_level, status FROM approval_queue "
        "WHERE queue_id = ?",
        (queue_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Queue ID inconnu : {queue_id}")
    if row["status"] != STATUS_PENDING:
        raise ValueError(
            f"Queue ID {queue_id} déjà traité (statut: {row['status']})"
        )

    if rng is None:
        rng = random.Random()
    # Ajuste mean/std si supervisor (L4)
    if row["autonomy_level"] == AUTONOMY_LEVEL_L4:
        mean_lag_minutes *= 2.0
        std_lag_minutes *= 2.0
    sampled_lag = max(1.0, rng.gauss(mean_lag_minutes, std_lag_minutes))

    submitted = datetime.fromisoformat(row["submitted_at"])
    approved_at = submitted + timedelta(minutes=sampled_lag)

    conn.execute(
        """
        UPDATE approval_queue SET
            status = ?, approved_at = ?, approved_by = ?,
            approval_lag_min = ?
        WHERE queue_id = ?
        """,
        (STATUS_APPROVED, approved_at.isoformat(),
         f"auto:simulation:{sampled_lag:.1f}min",
         sampled_lag, queue_id),
    )
    return _fetch_entry(conn, queue_id)


def list_pending(
    conn: sqlite3.Connection,
    *,
    autonomy_level: str | None = None,
    limit: int = 100,
) -> list[ApprovalEntry]:
    """Renvoie les décisions en attente, triées par submitted_at ASC."""
    sql = (
        "SELECT * FROM approval_queue WHERE status = ?"
    )
    params: list = [STATUS_PENDING]
    if autonomy_level is not None:
        sql += " AND autonomy_level = ?"
        params.append(autonomy_level)
    sql += " ORDER BY submitted_at ASC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [_row_to_entry(r) for r in rows]


def _fetch_entry(conn: sqlite3.Connection, queue_id: int) -> ApprovalEntry:
    row = conn.execute(
        "SELECT * FROM approval_queue WHERE queue_id = ?",
        (queue_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"Entrée disparue : {queue_id}")
    return _row_to_entry(row)


def _row_to_entry(row) -> ApprovalEntry:
    return ApprovalEntry(
        queue_id=int(row["queue_id"]),
        decision_id=int(row["decision_id"]),
        autonomy_level=row["autonomy_level"],
        status=row["status"],
        submitted_at=row["submitted_at"],
        approved_at=row["approved_at"],
        approved_by=row["approved_by"],
        approval_lag_min=(
            float(row["approval_lag_min"])
            if row["approval_lag_min"] is not None else None
        ),
        notes=row["notes"],
    )
