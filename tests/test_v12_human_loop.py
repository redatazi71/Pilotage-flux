"""Tests V12.4 — Workflow humain (rôles, audit, escalation, dashboard)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from pilotage_flux.cybernetic.delta_engine import (
    AUTONOMY_LEVEL_L3,
    AUTONOMY_LEVEL_L4,
    submit_to_approval_queue,
)
from pilotage_flux.cybernetic.human_loop import (
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_SUPERVISOR,
    ROLES,
    AuditEvent,
    can_approve,
    detect_overdue,
    escalate_overdue,
    get_user_role,
    list_audit_events,
    list_notifications,
    log_audit_event,
    notify,
    set_user_role,
    snapshot_dashboard,
)
from pilotage_flux.cybernetic.human_loop.audit_log import (
    EVENT_APPROVED,
    EVENT_ESCALATED,
    EVENT_SUBMITTED,
    EVENT_TYPES,
)
from pilotage_flux.cybernetic.human_loop.notifications import (
    KIND_OVERDUE,
    KIND_PENDING_APPROVAL,
    mark_read,
)
from pilotage_flux.db import db_session


def _seed_dev_and_decision(
    conn: sqlite3.Connection, action_level: str = "replan_local",
) -> int:
    """Insère event_deviation + tolerance_filter_decisions, renvoie decision_id."""
    cur = conn.execute(
        "INSERT INTO event_deviations "
        "(deviation_kind, delta_value, score, qualification) "
        "VALUES ('time_delta', 30.0, 0.5, 'medium')"
    )
    deviation_id = int(cur.lastrowid)
    cur = conn.execute(
        """
        INSERT INTO tolerance_filter_decisions
            (deviation_id, candidate_id, score_magnitude,
             frequency_in_window, score_combined, action_level,
             latency_minutes, decided_at)
        VALUES (?, NULL, 0.5, 1, 0.5, ?, 0, datetime('now'))
        """,
        (deviation_id, action_level),
    )
    return int(cur.lastrowid)


# ---------------------------------------------------------------------
# Rôles
# ---------------------------------------------------------------------


def test_operator_can_approve_L3_only() -> None:
    assert can_approve(ROLE_OPERATOR, AUTONOMY_LEVEL_L3) is True
    assert can_approve(ROLE_OPERATOR, AUTONOMY_LEVEL_L4) is False


def test_supervisor_can_approve_L3_and_L4() -> None:
    assert can_approve(ROLE_SUPERVISOR, AUTONOMY_LEVEL_L3) is True
    assert can_approve(ROLE_SUPERVISOR, AUTONOMY_LEVEL_L4) is True


def test_admin_can_approve_everything() -> None:
    assert can_approve(ROLE_ADMIN, AUTONOMY_LEVEL_L3) is True
    assert can_approve(ROLE_ADMIN, AUTONOMY_LEVEL_L4) is True


def test_unknown_role_cannot_approve() -> None:
    assert can_approve("intern", AUTONOMY_LEVEL_L3) is False


def test_set_and_get_user_role(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        set_user_role(conn, "alice@x.fr", ROLE_OPERATOR)
        assert get_user_role(conn, "alice@x.fr") == ROLE_OPERATOR
        # update
        set_user_role(conn, "alice@x.fr", ROLE_SUPERVISOR)
        assert get_user_role(conn, "alice@x.fr") == ROLE_SUPERVISOR


def test_set_role_rejects_unknown(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        with pytest.raises(ValueError, match="inconnu"):
            set_user_role(conn, "alice@x.fr", "intern")


def test_get_user_role_returns_none_if_absent(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        assert get_user_role(conn, "ghost@x.fr") is None


# ---------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------


def test_log_audit_event_persists(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        d_id = _seed_dev_and_decision(conn)
        qid = submit_to_approval_queue(conn, d_id, AUTONOMY_LEVEL_L3)
        log_id = log_audit_event(
            conn, event_type=EVENT_SUBMITTED, actor="human:alice",
            queue_id=qid, details={"foo": "bar"},
        )
        assert log_id > 0
        events = list_audit_events(conn)
        assert len(events) == 1
        assert events[0].event_type == EVENT_SUBMITTED
        assert events[0].actor == "human:alice"
        assert events[0].queue_id == qid
        assert events[0].details == {"foo": "bar"}


def test_log_audit_event_rejects_unknown_type(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        with pytest.raises(ValueError):
            log_audit_event(
                conn, event_type="invented", actor="human:alice",
            )


def test_list_audit_filters_by_queue(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        d1 = _seed_dev_and_decision(conn)
        d2 = _seed_dev_and_decision(conn, action_level="replan_global")
        q1 = submit_to_approval_queue(conn, d1, AUTONOMY_LEVEL_L3)
        q2 = submit_to_approval_queue(conn, d2, AUTONOMY_LEVEL_L4)
        log_audit_event(conn, event_type=EVENT_SUBMITTED,
                         actor="x", queue_id=q1)
        log_audit_event(conn, event_type=EVENT_APPROVED,
                         actor="x", queue_id=q2)
        log_audit_event(conn, event_type=EVENT_SUBMITTED,
                         actor="x", queue_id=q1)
        e1 = list_audit_events(conn, queue_id=q1)
        e2 = list_audit_events(conn, queue_id=q2)
        assert len(e1) == 2
        assert len(e2) == 1


# ---------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------


def test_notify_creates_entry(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        d_id = _seed_dev_and_decision(conn)
        qid = submit_to_approval_queue(conn, d_id, AUTONOMY_LEVEL_L3)
        nid = notify(
            conn, target="role:operator",
            kind=KIND_PENDING_APPROVAL,
            message=f"Queue {qid} attend votre validation",
            queue_id=qid,
        )
        assert nid > 0
        notifs = list_notifications(conn, target="role:operator")
        assert len(notifs) == 1
        assert notifs[0].kind == KIND_PENDING_APPROVAL


def test_notify_rejects_invalid_target(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        with pytest.raises(ValueError, match="target"):
            notify(conn, target="invalid",
                    kind=KIND_PENDING_APPROVAL, message="x")


def test_notify_rejects_unknown_kind(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        with pytest.raises(ValueError, match="kind"):
            notify(conn, target="role:operator",
                    kind="bogus", message="x")


def test_mark_read_hides_from_unread_list(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        nid = notify(conn, target="user:alice",
                      kind=KIND_PENDING_APPROVAL, message="hi")
        assert len(list_notifications(conn, only_unread=True)) == 1
        mark_read(conn, nid)
        assert len(list_notifications(conn, only_unread=True)) == 0
        assert len(list_notifications(conn, only_unread=False)) == 1


# ---------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------


def test_detect_overdue_no_pending_returns_empty(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        assert detect_overdue(conn) == []


def test_detect_overdue_finds_old_pending(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        decision_id = _seed_dev_and_decision(conn)
        qid = submit_to_approval_queue(
            conn, decision_id, AUTONOMY_LEVEL_L3,
        )
        # Force le submitted_at à il y a 6h
        conn.execute(
            "UPDATE approval_queue SET submitted_at = ? "
            "WHERE queue_id = ?",
            ((datetime.utcnow() - timedelta(hours=6)).isoformat(), qid),
        )
        overdue = detect_overdue(conn, overdue_threshold_minutes=240.0)
        assert len(overdue) == 1
        assert overdue[0]["queue_id"] == qid
        assert overdue[0]["lag_min"] > 240


def test_escalate_L3_to_L4_creates_audit_and_notifications(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        decision_id = _seed_dev_and_decision(conn)
        qid = submit_to_approval_queue(
            conn, decision_id, AUTONOMY_LEVEL_L3,
        )
        # Force le submitted_at à il y a 5h
        conn.execute(
            "UPDATE approval_queue SET submitted_at = ? "
            "WHERE queue_id = ?",
            ((datetime.utcnow() - timedelta(hours=5)).isoformat(), qid),
        )
        result = escalate_overdue(conn, overdue_threshold_minutes=240.0)
        assert result.n_escalated == 1
        assert result.n_notified >= 2  # overdue + escalated
        # L'entrée est maintenant en L4
        new_level = conn.execute(
            "SELECT autonomy_level FROM approval_queue WHERE queue_id = ?",
            (qid,),
        ).fetchone()["autonomy_level"]
        assert new_level == AUTONOMY_LEVEL_L4
        # Audit trace l'escalation
        events = list_audit_events(conn, event_type=EVENT_ESCALATED)
        assert len(events) == 1
        # Notification supervisor existe
        notifs = list_notifications(conn, target="role:supervisor")
        assert any(n.kind == "escalated" for n in notifs)


def test_escalate_idempotent_for_L4(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        decision_id = _seed_dev_and_decision(
            conn, action_level="replan_global",
        )
        qid = submit_to_approval_queue(
            conn, decision_id, AUTONOMY_LEVEL_L4,
        )
        conn.execute(
            "UPDATE approval_queue SET submitted_at = ? "
            "WHERE queue_id = ?",
            ((datetime.utcnow() - timedelta(hours=8)).isoformat(), qid),
        )
        result = escalate_overdue(conn, overdue_threshold_minutes=240.0)
        # L4 ne s'escalade pas plus haut, mais notifie le supervisor
        assert result.n_escalated == 0
        assert result.n_notified == 1
        events = list_audit_events(conn, event_type=EVENT_ESCALATED)
        assert len(events) == 0  # pas de log d'escalation pour L4 déjà L4


# ---------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------


def test_snapshot_dashboard_empty(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        snap = snapshot_dashboard(conn)
        assert snap.pending_total == 0
        assert snap.approved_last_24h == 0


def test_snapshot_dashboard_counts_pending_by_level(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        d1 = _seed_dev_and_decision(conn)
        d2 = _seed_dev_and_decision(conn, action_level="replan_global")
        submit_to_approval_queue(conn, d1, AUTONOMY_LEVEL_L3)
        submit_to_approval_queue(conn, d2, AUTONOMY_LEVEL_L4)
        snap = snapshot_dashboard(conn)
        assert snap.pending_total == 2
        assert snap.pending_l3 == 1
        assert snap.pending_l4 == 1


def test_snapshot_dashboard_includes_overdue(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        d1 = _seed_dev_and_decision(conn)
        qid = submit_to_approval_queue(conn, d1, AUTONOMY_LEVEL_L3)
        conn.execute(
            "UPDATE approval_queue SET submitted_at = ? "
            "WHERE queue_id = ?",
            ((datetime.utcnow() - timedelta(hours=5)).isoformat(), qid),
        )
        snap = snapshot_dashboard(conn, overdue_threshold_minutes=240.0)
        assert snap.pending_overdue == 1
        assert snap.longest_pending_min > 240


def test_snapshot_dashboard_notifications_per_role(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        notify(conn, target="role:operator",
                kind=KIND_PENDING_APPROVAL, message="x")
        notify(conn, target="role:operator",
                kind=KIND_PENDING_APPROVAL, message="y")
        notify(conn, target="role:supervisor",
                kind=KIND_OVERDUE, message="z")
        snap = snapshot_dashboard(conn)
        assert snap.notifications_unread_by_role.get("operator") == 2
        assert snap.notifications_unread_by_role.get("supervisor") == 1
