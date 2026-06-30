"""V12.4 — Audit trail immuable des transitions d'approbation.

Chaque transition d'état (submit / approve / reject / escalate /
role_change / etc.) est tracée dans `approval_audit_log` avec :

  - queue_id    : référence vers approval_queue (NULL pour les role_change)
  - event_type  : type de transition
  - actor       : qui (humain ou système)
  - details     : payload JSON libre
  - occurred_at : timestamp UTC

Le log est append-only — aucune fonction d'édition n'est exposée.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


EVENT_SUBMITTED = "submitted"
EVENT_APPROVED = "approved"
EVENT_REJECTED = "rejected"
EVENT_ESCALATED = "escalated"
EVENT_AUTO_TIMEOUT = "auto_timeout"
EVENT_ROLE_CHANGED = "role_changed"
EVENT_NOTE_ADDED = "note_added"

EVENT_TYPES = (
    EVENT_SUBMITTED, EVENT_APPROVED, EVENT_REJECTED, EVENT_ESCALATED,
    EVENT_AUTO_TIMEOUT, EVENT_ROLE_CHANGED, EVENT_NOTE_ADDED,
)


@dataclass(frozen=True)
class AuditEvent:
    log_id: int
    queue_id: int | None
    event_type: str
    actor: str
    details: dict
    occurred_at: str


def log_audit_event(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    actor: str,
    queue_id: int | None = None,
    details: dict | None = None,
) -> int:
    """Ajoute une ligne d'audit. Renvoie log_id."""
    if event_type not in EVENT_TYPES:
        raise ValueError(
            f"event_type inconnu : {event_type!r} (attendu : {EVENT_TYPES})"
        )
    details_json = json.dumps(details or {}, ensure_ascii=False)
    cur = conn.execute(
        """
        INSERT INTO approval_audit_log
            (queue_id, event_type, actor, details)
        VALUES (?, ?, ?, ?)
        """,
        (queue_id, event_type, actor, details_json),
    )
    return int(cur.lastrowid)


def list_audit_events(
    conn: sqlite3.Connection,
    *,
    queue_id: int | None = None,
    event_type: str | None = None,
    limit: int = 200,
) -> list[AuditEvent]:
    """Liste les événements d'audit, plus récents en premier."""
    sql = "SELECT * FROM approval_audit_log WHERE 1=1"
    params: list = []
    if queue_id is not None:
        sql += " AND queue_id = ?"
        params.append(queue_id)
    if event_type is not None:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"event_type inconnu : {event_type!r}")
        sql += " AND event_type = ?"
        params.append(event_type)
    sql += " ORDER BY occurred_at DESC, log_id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [
        AuditEvent(
            log_id=int(r["log_id"]),
            queue_id=int(r["queue_id"]) if r["queue_id"] is not None else None,
            event_type=r["event_type"],
            actor=r["actor"],
            details=json.loads(r["details"]) if r["details"] else {},
            occurred_at=r["occurred_at"],
        )
        for r in rows
    ]
