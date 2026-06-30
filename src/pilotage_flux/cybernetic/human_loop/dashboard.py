"""V12.4 — Dashboard d'agrégation pour visualisation V12.

Calcule des statistiques sur la queue d'approbation, l'audit et les
notifications pour un affichage CLI / UI.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class DashboardSnapshot:
    """État instantané du workflow V12.4."""

    pending_total: int = 0
    pending_l3: int = 0
    pending_l4: int = 0
    pending_overdue: int = 0
    longest_pending_min: float = 0.0
    approved_last_24h: int = 0
    rejected_last_24h: int = 0
    escalated_last_24h: int = 0
    avg_approval_lag_min: float = 0.0
    notifications_unread_by_role: dict[str, int] = field(default_factory=dict)


def snapshot_dashboard(
    conn: sqlite3.Connection,
    *,
    overdue_threshold_minutes: float = 240.0,
    now: datetime | None = None,
) -> DashboardSnapshot:
    """Construit un snapshot synthétique du workflow V12.4."""
    if now is None:
        now = datetime.utcnow()
    snap = DashboardSnapshot()

    # Pending counts
    counts = conn.execute(
        """
        SELECT autonomy_level, COUNT(*) AS n
        FROM approval_queue
        WHERE status = 'pending'
        GROUP BY autonomy_level
        """
    ).fetchall()
    for r in counts:
        level = r["autonomy_level"]
        n = int(r["n"])
        if level.startswith("L3_"):
            snap.pending_l3 += n
        elif level.startswith("L4_"):
            snap.pending_l4 += n
        snap.pending_total += n

    # Longest pending + overdue count
    rows = conn.execute(
        "SELECT submitted_at FROM approval_queue WHERE status = 'pending'"
    ).fetchall()
    longest = 0.0
    overdue_count = 0
    for r in rows:
        try:
            submitted = datetime.fromisoformat(r["submitted_at"])
        except (ValueError, TypeError):
            continue
        lag = (now - submitted).total_seconds() / 60.0
        longest = max(longest, lag)
        if lag >= overdue_threshold_minutes:
            overdue_count += 1
    snap.longest_pending_min = longest
    snap.pending_overdue = overdue_count

    # Stats sur 24h glissantes
    cutoff = now.timestamp() - 24 * 3600
    rows = conn.execute(
        """
        SELECT status, approved_at, approval_lag_min FROM approval_queue
        WHERE status != 'pending' AND approved_at IS NOT NULL
        """
    ).fetchall()
    lag_sum = 0.0
    lag_count = 0
    for r in rows:
        try:
            d = datetime.fromisoformat(r["approved_at"]).timestamp()
        except (ValueError, TypeError):
            continue
        if d < cutoff:
            continue
        if r["status"] == "approved":
            snap.approved_last_24h += 1
        elif r["status"] == "rejected":
            snap.rejected_last_24h += 1
        if r["approval_lag_min"] is not None:
            lag_sum += float(r["approval_lag_min"])
            lag_count += 1

    snap.avg_approval_lag_min = (
        round(lag_sum / lag_count, 1) if lag_count > 0 else 0.0
    )

    # Escalations 24h
    rows = conn.execute(
        """
        SELECT occurred_at FROM approval_audit_log
        WHERE event_type = 'escalated'
        """
    ).fetchall()
    for r in rows:
        try:
            t = datetime.fromisoformat(r["occurred_at"]).timestamp()
        except (ValueError, TypeError):
            continue
        if t >= cutoff:
            snap.escalated_last_24h += 1

    # Notifications par rôle (unread)
    rows = conn.execute(
        """
        SELECT target, COUNT(*) AS n FROM notifications
        WHERE read_at IS NULL AND target LIKE 'role:%'
        GROUP BY target
        """
    ).fetchall()
    for r in rows:
        role = r["target"].split(":", 1)[1]
        snap.notifications_unread_by_role[role] = int(r["n"])

    return snap
