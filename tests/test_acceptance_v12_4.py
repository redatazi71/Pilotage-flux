"""V12.4 — Acceptance E2E : workflow humain complet bout-en-bout."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from pilotage_flux.cybernetic.delta_engine import (
    AUTONOMY_LEVEL_L3,
    approve_decision,
    submit_to_approval_queue,
)
from pilotage_flux.cybernetic.human_loop import (
    ROLE_OPERATOR,
    ROLE_SUPERVISOR,
    can_approve,
    escalate_overdue,
    list_audit_events,
    list_notifications,
    log_audit_event,
    notify,
    set_user_role,
    snapshot_dashboard,
)
from pilotage_flux.cybernetic.human_loop.audit_log import (
    EVENT_APPROVED,
    EVENT_SUBMITTED,
)
from pilotage_flux.cybernetic.human_loop.notifications import (
    KIND_PENDING_APPROVAL,
)
from pilotage_flux.db import db_session


def _seed_decision(
    conn: sqlite3.Connection, action_level: str = "replan_local",
) -> int:
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


def test_v12_4_e2e_full_human_workflow(tmp_db) -> None:
    """E2E V12.4 : seed users, déclenche décision, notifie, opérateur
    approuve, audit complet, dashboard cohérent."""
    with db_session(tmp_db) as conn:
        # 1. Setup rôles
        set_user_role(conn, "alice@x.fr", ROLE_OPERATOR)
        set_user_role(conn, "bob@x.fr", ROLE_SUPERVISOR)

        # 2. Décision L3 enqueue
        d_id = _seed_decision(conn)
        qid = submit_to_approval_queue(conn, d_id, AUTONOMY_LEVEL_L3)
        log_audit_event(
            conn, event_type=EVENT_SUBMITTED,
            actor="auto:dispatcher", queue_id=qid,
        )
        notify(
            conn, target=f"role:{ROLE_OPERATOR}",
            kind=KIND_PENDING_APPROVAL,
            message=f"Queue {qid} pending validation L3",
            queue_id=qid,
        )

        # 3. Opérateur Alice vérifie ses permissions et approuve
        role = "operator"
        assert can_approve(role, AUTONOMY_LEVEL_L3) is True
        entry = approve_decision(
            conn, qid, approved_by="human:alice@x.fr",
            notes="Validé après check",
        )
        log_audit_event(
            conn, event_type=EVENT_APPROVED,
            actor="human:alice@x.fr", queue_id=qid,
            details={"lag_min": entry.approval_lag_min},
        )

        # 4. Audit trail cohérent (submitted + approved)
        audit = list_audit_events(conn, queue_id=qid)
        assert len(audit) == 2
        types = {e.event_type for e in audit}
        assert EVENT_SUBMITTED in types
        assert EVENT_APPROVED in types

        # 5. Dashboard reflète l'état
        snap = snapshot_dashboard(conn)
        assert snap.pending_total == 0
        assert snap.approved_last_24h == 1


def test_v12_4_e2e_escalation_pipeline(tmp_db) -> None:
    """E2E V12.4 : décision L3 en attente trop longue → escalation
    automatique vers L4 + audit + notification supervisor."""
    with db_session(tmp_db) as conn:
        set_user_role(conn, "carol@x.fr", ROLE_SUPERVISOR)

        # 1. Décision L3 en attente depuis 5h
        d_id = _seed_decision(conn)
        qid = submit_to_approval_queue(conn, d_id, AUTONOMY_LEVEL_L3)
        log_audit_event(
            conn, event_type=EVENT_SUBMITTED,
            actor="auto:dispatcher", queue_id=qid,
        )
        # Force submitted_at à il y a 5h
        conn.execute(
            "UPDATE approval_queue SET submitted_at = ? "
            "WHERE queue_id = ?",
            (
                (datetime.utcnow() - timedelta(hours=5)).isoformat(),
                qid,
            ),
        )

        # 2. Job d'escalation tourne
        result = escalate_overdue(conn, overdue_threshold_minutes=240.0)
        assert result.n_escalated == 1

        # 3. La queue est maintenant en L4
        new_level = conn.execute(
            "SELECT autonomy_level FROM approval_queue WHERE queue_id = ?",
            (qid,),
        ).fetchone()["autonomy_level"]
        assert new_level == "L4_global_replan_approval"

        # 4. Le supervisor reçoit la notification
        notifs_super = list_notifications(
            conn, target=f"role:{ROLE_SUPERVISOR}",
        )
        kinds = {n.kind for n in notifs_super}
        assert "escalated" in kinds

        # 5. Audit trail trace l'escalation
        audit = list_audit_events(conn, queue_id=qid)
        types = {e.event_type for e in audit}
        assert "escalated" in types

        # 6. Maintenant le supervisor peut approuver (operator ne pourrait pas)
        assert can_approve(ROLE_SUPERVISOR, "L4_global_replan_approval") is True
        assert can_approve(ROLE_OPERATOR, "L4_global_replan_approval") is False
