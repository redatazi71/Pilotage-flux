"""V12.4 — Escalation automatique des approbations en retard.

Quand une décision L3 ou L4 reste en `pending` au-delà d'un seuil
(`overdue_threshold_minutes`), elle est :

  1. **Notifiée** au rôle target avec kind=KIND_OVERDUE
  2. **Si L3** : escaladée à L4 (autonomy_level mis à jour) + notif
     supervisor
  3. **Auditée** via log_audit_event(EVENT_ESCALATED)

L'escalation est volontairement **explicite** (pas de timeout
automatique caché) — appelée via CLI `human-escalate` ou via une
tâche planifiée.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from pilotage_flux.cybernetic.delta_engine.autonomy_levels import (
    AUTONOMY_LEVEL_L3,
    AUTONOMY_LEVEL_L4,
)
from pilotage_flux.cybernetic.human_loop.audit_log import (
    EVENT_ESCALATED,
    log_audit_event,
)
from pilotage_flux.cybernetic.human_loop.notifications import (
    KIND_ESCALATED,
    KIND_OVERDUE,
    notify,
)
from pilotage_flux.cybernetic.human_loop.roles import (
    ROLE_OPERATOR,
    ROLE_SUPERVISOR,
)

DEFAULT_OVERDUE_MINUTES = 240.0  # 4 heures pour L3, escaladé en L4 si dépassé


@dataclass(frozen=True)
class EscalationResult:
    """Résultat d'une opération d'escalation."""

    n_detected: int
    n_escalated: int
    n_notified: int
    escalated_queue_ids: list[int]


def detect_overdue(
    conn: sqlite3.Connection,
    *,
    overdue_threshold_minutes: float = DEFAULT_OVERDUE_MINUTES,
    now: datetime | None = None,
) -> list[dict]:
    """Renvoie les entrées pending dont le lag dépasse le seuil."""
    if now is None:
        now = datetime.utcnow()
    rows = conn.execute(
        """
        SELECT queue_id, decision_id, autonomy_level,
               submitted_at, notes
        FROM approval_queue
        WHERE status = 'pending'
        """
    ).fetchall()
    overdue = []
    for r in rows:
        try:
            submitted = datetime.fromisoformat(r["submitted_at"])
        except (ValueError, TypeError):
            continue
        lag_min = (now - submitted).total_seconds() / 60.0
        if lag_min >= overdue_threshold_minutes:
            overdue.append({
                "queue_id": int(r["queue_id"]),
                "decision_id": int(r["decision_id"]),
                "autonomy_level": r["autonomy_level"],
                "lag_min": lag_min,
                "notes": r["notes"],
            })
    return overdue


def escalate_overdue(
    conn: sqlite3.Connection,
    *,
    overdue_threshold_minutes: float = DEFAULT_OVERDUE_MINUTES,
    actor: str = "auto:escalation_job",
    now: datetime | None = None,
) -> EscalationResult:
    """Détecte les overdue et applique l'escalation L3 → L4 +
    notifications + audit. Idempotent : si l'entrée est déjà L4, on
    ne fait que notifier."""
    overdue = detect_overdue(
        conn,
        overdue_threshold_minutes=overdue_threshold_minutes,
        now=now,
    )
    n_escalated = 0
    n_notified = 0
    escalated_ids = []
    for entry in overdue:
        qid = entry["queue_id"]
        current_level = entry["autonomy_level"]
        lag_min = entry["lag_min"]

        # Notification overdue (rôle correspondant)
        target_role = (
            ROLE_OPERATOR
            if current_level == AUTONOMY_LEVEL_L3
            else ROLE_SUPERVISOR
        )
        notify(
            conn,
            target=f"role:{target_role}",
            kind=KIND_OVERDUE,
            queue_id=qid,
            message=(
                f"Queue {qid} en attente depuis {lag_min:.0f} min "
                f"(niveau {current_level})"
            ),
        )
        n_notified += 1

        # Si L3 → escalade à L4
        if current_level == AUTONOMY_LEVEL_L3:
            conn.execute(
                "UPDATE approval_queue SET autonomy_level = ? "
                "WHERE queue_id = ?",
                (AUTONOMY_LEVEL_L4, qid),
            )
            log_audit_event(
                conn,
                event_type=EVENT_ESCALATED,
                actor=actor,
                queue_id=qid,
                details={
                    "from_level": AUTONOMY_LEVEL_L3,
                    "to_level": AUTONOMY_LEVEL_L4,
                    "lag_min": lag_min,
                },
            )
            notify(
                conn,
                target=f"role:{ROLE_SUPERVISOR}",
                kind=KIND_ESCALATED,
                queue_id=qid,
                message=(
                    f"Queue {qid} escaladée L3 → L4 "
                    f"(opérateur non répondu après {lag_min:.0f} min)"
                ),
            )
            n_notified += 1
            n_escalated += 1
            escalated_ids.append(qid)

    return EscalationResult(
        n_detected=len(overdue),
        n_escalated=n_escalated,
        n_notified=n_notified,
        escalated_queue_ids=escalated_ids,
    )
