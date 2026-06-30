"""V12.4 — Système de notifications V12.

Les notifications sont persistées en DB (table `notifications`) pour
audit. Trois canaux logiques :

  - **role:<role>** : notifie tous les utilisateurs ayant ce rôle
  - **user:<user_id>** : notifie un utilisateur précis

Le dispatcher est ici minimal (insère en DB) ; un adapter futur
pourrait l'écouter pour pousser sur email / Slack / Teams.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


KIND_PENDING_APPROVAL = "pending_approval"
KIND_OVERDUE = "overdue"
KIND_ESCALATED = "escalated"
KIND_REJECTED_WITH_NOTE = "rejected_with_note"

NOTIFICATION_KINDS = (
    KIND_PENDING_APPROVAL, KIND_OVERDUE,
    KIND_ESCALATED, KIND_REJECTED_WITH_NOTE,
)


@dataclass(frozen=True)
class Notification:
    notification_id: int
    target: str
    kind: str
    queue_id: int | None
    message: str
    read_at: str | None
    created_at: str


def notify(
    conn: sqlite3.Connection,
    *,
    target: str,
    kind: str,
    message: str,
    queue_id: int | None = None,
) -> int:
    """Crée une notification pour un destinataire (role: ou user:)."""
    if kind not in NOTIFICATION_KINDS:
        raise ValueError(
            f"kind inconnu : {kind!r} (attendu : {NOTIFICATION_KINDS})"
        )
    if not target.startswith(("role:", "user:")):
        raise ValueError(
            f"target invalide : {target!r} "
            f"(préfixe attendu : 'role:' ou 'user:')"
        )
    cur = conn.execute(
        """
        INSERT INTO notifications (target, kind, queue_id, message)
        VALUES (?, ?, ?, ?)
        """,
        (target, kind, queue_id, message),
    )
    return int(cur.lastrowid)


def list_notifications(
    conn: sqlite3.Connection,
    *,
    target: str | None = None,
    only_unread: bool = True,
    limit: int = 50,
) -> list[Notification]:
    """Liste les notifications, plus récentes en premier."""
    sql = "SELECT * FROM notifications WHERE 1=1"
    params: list = []
    if target is not None:
        sql += " AND target = ?"
        params.append(target)
    if only_unread:
        sql += " AND read_at IS NULL"
    sql += " ORDER BY created_at DESC, notification_id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [_row_to_notif(r) for r in rows]


def mark_read(
    conn: sqlite3.Connection, notification_id: int,
) -> None:
    """Marque une notification comme lue (timestamp now)."""
    conn.execute(
        "UPDATE notifications SET read_at = datetime('now') "
        "WHERE notification_id = ? AND read_at IS NULL",
        (notification_id,),
    )


def _row_to_notif(row) -> Notification:
    return Notification(
        notification_id=int(row["notification_id"]),
        target=row["target"],
        kind=row["kind"],
        queue_id=(
            int(row["queue_id"]) if row["queue_id"] is not None else None
        ),
        message=row["message"],
        read_at=row["read_at"],
        created_at=row["created_at"],
    )
