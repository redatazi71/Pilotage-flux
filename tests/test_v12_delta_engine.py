"""Tests V12.3 — Delta engine 4 niveaux d'autonomie + approval queue."""

from __future__ import annotations

import random
import sqlite3

import pytest

from pilotage_flux.cybernetic.delta_engine import (
    AUTONOMY_LEVEL_L1,
    AUTONOMY_LEVEL_L2,
    AUTONOMY_LEVEL_L3,
    AUTONOMY_LEVEL_L4,
    REQUIRES_APPROVAL,
    approve_decision,
    auto_approve_with_lag,
    classify_autonomy_level,
    describe_level,
    dispatch_decision,
    list_pending,
    reject_decision,
    submit_to_approval_queue,
)
from pilotage_flux.cybernetic.delta_engine.approval_queue import (
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
)
from pilotage_flux.db import db_session, init_schema
from pilotage_flux.events_v3.dual_tolerance import (
    ACTION_CORRECT_LOCAL,
    ACTION_ESCALATE,
    ACTION_INFORM,
    ACTION_REPLAN_GLOBAL,
    ACTION_REPLAN_LOCAL,
    ACTION_WATCH,
)


def _insert_dummy_decision(conn: sqlite3.Connection, action_level: str) -> int:
    """Crée une décision factice dans tolerance_filter_decisions
    (insère aussi une event_deviation pour respecter la FK)."""
    cur = conn.execute(
        """
        INSERT INTO event_deviations
            (deviation_kind, delta_value, score, qualification)
        VALUES ('time_delta', 30.0, 0.5, 'medium')
        """,
    )
    deviation_id = int(cur.lastrowid)
    cur = conn.execute(
        """
        INSERT INTO tolerance_filter_decisions
            (deviation_id, candidate_id, score_magnitude,
             frequency_in_window, score_combined, action_level,
             latency_minutes, decided_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (deviation_id, None, 0.5, 1, 0.5, action_level, 0),
    )
    return int(cur.lastrowid)


# ---------------------------------------------------------------------
# classify_autonomy_level
# ---------------------------------------------------------------------


def test_classify_inform_and_watch_to_L1() -> None:
    assert classify_autonomy_level(ACTION_INFORM) == AUTONOMY_LEVEL_L1
    assert classify_autonomy_level(ACTION_WATCH) == AUTONOMY_LEVEL_L1


def test_classify_correct_local_to_L2() -> None:
    assert classify_autonomy_level(ACTION_CORRECT_LOCAL) == AUTONOMY_LEVEL_L2


def test_classify_replan_local_to_L3() -> None:
    assert classify_autonomy_level(ACTION_REPLAN_LOCAL) == AUTONOMY_LEVEL_L3


def test_classify_escalate_and_replan_global_to_L4() -> None:
    assert classify_autonomy_level(ACTION_ESCALATE) == AUTONOMY_LEVEL_L4
    assert classify_autonomy_level(ACTION_REPLAN_GLOBAL) == AUTONOMY_LEVEL_L4


def test_classify_unknown_defaults_to_L1() -> None:
    assert classify_autonomy_level("unknown_action") == AUTONOMY_LEVEL_L1


def test_describe_level_returns_text() -> None:
    for lvl in (AUTONOMY_LEVEL_L1, AUTONOMY_LEVEL_L2,
                AUTONOMY_LEVEL_L3, AUTONOMY_LEVEL_L4):
        text = describe_level(lvl)
        assert len(text) > 20
    assert "inconnu" in describe_level("invalid")


def test_only_L3_L4_require_approval() -> None:
    assert AUTONOMY_LEVEL_L1 not in REQUIRES_APPROVAL
    assert AUTONOMY_LEVEL_L2 not in REQUIRES_APPROVAL
    assert AUTONOMY_LEVEL_L3 in REQUIRES_APPROVAL
    assert AUTONOMY_LEVEL_L4 in REQUIRES_APPROVAL


# ---------------------------------------------------------------------
# submit / approve / reject
# ---------------------------------------------------------------------


def test_submit_creates_pending_entry(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        decision_id = _insert_dummy_decision(conn, ACTION_REPLAN_LOCAL)
        queue_id = submit_to_approval_queue(
            conn, decision_id, AUTONOMY_LEVEL_L3,
        )
        pending = list_pending(conn)
        assert len(pending) == 1
        assert pending[0].queue_id == queue_id
        assert pending[0].status == STATUS_PENDING
        assert pending[0].autonomy_level == AUTONOMY_LEVEL_L3


def test_submit_rejects_L1_L2(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        decision_id = _insert_dummy_decision(conn, ACTION_INFORM)
        with pytest.raises(ValueError, match="ne requiert pas"):
            submit_to_approval_queue(
                conn, decision_id, AUTONOMY_LEVEL_L1,
            )
        with pytest.raises(ValueError, match="ne requiert pas"):
            submit_to_approval_queue(
                conn, decision_id, AUTONOMY_LEVEL_L2,
            )


def test_approve_changes_status_and_traces_lag(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        decision_id = _insert_dummy_decision(conn, ACTION_REPLAN_LOCAL)
        queue_id = submit_to_approval_queue(
            conn, decision_id, AUTONOMY_LEVEL_L3,
        )
        entry = approve_decision(conn, queue_id, approved_by="human:alice")
        assert entry.status == STATUS_APPROVED
        assert entry.approved_by == "human:alice"
        assert entry.approval_lag_min is not None
        assert entry.approval_lag_min >= 0
        assert list_pending(conn) == []


def test_reject_changes_status(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        decision_id = _insert_dummy_decision(conn, ACTION_REPLAN_GLOBAL)
        queue_id = submit_to_approval_queue(
            conn, decision_id, AUTONOMY_LEVEL_L4,
        )
        entry = reject_decision(
            conn, queue_id, rejected_by="human:bob",
            notes="trop large impact",
        )
        assert entry.status == STATUS_REJECTED
        assert entry.notes == "trop large impact"


def test_double_approve_raises(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        decision_id = _insert_dummy_decision(conn, ACTION_REPLAN_LOCAL)
        queue_id = submit_to_approval_queue(
            conn, decision_id, AUTONOMY_LEVEL_L3,
        )
        approve_decision(conn, queue_id, approved_by="human:alice")
        with pytest.raises(ValueError, match="déjà traité"):
            approve_decision(conn, queue_id, approved_by="human:bob")


def test_auto_approve_with_lag_sets_lag_value(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        decision_id = _insert_dummy_decision(conn, ACTION_REPLAN_LOCAL)
        queue_id = submit_to_approval_queue(
            conn, decision_id, AUTONOMY_LEVEL_L3,
        )
        rng = random.Random(42)
        entry = auto_approve_with_lag(
            conn, queue_id, mean_lag_minutes=240.0, std_lag_minutes=60.0,
            rng=rng,
        )
        assert entry.status == STATUS_APPROVED
        assert entry.approved_by is not None
        assert entry.approved_by.startswith("auto:simulation:")
        assert entry.approval_lag_min > 60.0  # gaussian seed 42 around mean
        assert entry.approval_lag_min < 500.0


def test_auto_approve_L4_doubles_lag(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        decision_id_l3 = _insert_dummy_decision(conn, ACTION_REPLAN_LOCAL)
        decision_id_l4 = _insert_dummy_decision(conn, ACTION_REPLAN_GLOBAL)
        q3 = submit_to_approval_queue(
            conn, decision_id_l3, AUTONOMY_LEVEL_L3,
        )
        q4 = submit_to_approval_queue(
            conn, decision_id_l4, AUTONOMY_LEVEL_L4,
        )
        rng = random.Random(42)
        e3 = auto_approve_with_lag(conn, q3, rng=rng)
        rng = random.Random(42)
        e4 = auto_approve_with_lag(conn, q4, rng=rng)
        # L4 utilise le même tirage mais double mean+std → lag ~2× plus grand
        assert e4.approval_lag_min > e3.approval_lag_min * 1.5


# ---------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------


def test_dispatch_L1_returns_immediately_actionable(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        decision_id = _insert_dummy_decision(conn, ACTION_INFORM)
        result = dispatch_decision(conn, decision_id)
        assert result.autonomy_level == AUTONOMY_LEVEL_L1
        assert result.requires_approval is False
        assert result.queue_id is None
        assert result.immediately_actionable is True


def test_dispatch_L2_returns_immediately_actionable(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        decision_id = _insert_dummy_decision(conn, ACTION_CORRECT_LOCAL)
        result = dispatch_decision(conn, decision_id)
        assert result.autonomy_level == AUTONOMY_LEVEL_L2
        assert result.requires_approval is False
        assert result.immediately_actionable is True


def test_dispatch_L3_enqueues_and_blocks_immediate_action(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        decision_id = _insert_dummy_decision(conn, ACTION_REPLAN_LOCAL)
        result = dispatch_decision(conn, decision_id)
        assert result.autonomy_level == AUTONOMY_LEVEL_L3
        assert result.requires_approval is True
        assert result.queue_id is not None
        assert result.immediately_actionable is False
        # Vérifie qu'une entrée pending existe
        assert len(list_pending(conn)) == 1


def test_dispatch_L4_enqueues(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        decision_id = _insert_dummy_decision(conn, ACTION_REPLAN_GLOBAL)
        result = dispatch_decision(conn, decision_id)
        assert result.autonomy_level == AUTONOMY_LEVEL_L4
        assert result.requires_approval is True
        assert result.queue_id is not None


def test_dispatch_is_idempotent_for_L3_L4(tmp_db) -> None:
    """Double dispatch ne doit pas créer 2 entrées dans la queue."""
    with db_session(tmp_db) as conn:
        decision_id = _insert_dummy_decision(conn, ACTION_REPLAN_LOCAL)
        r1 = dispatch_decision(conn, decision_id)
        r2 = dispatch_decision(conn, decision_id)
        assert r1.queue_id == r2.queue_id
        assert len(list_pending(conn)) == 1


def test_dispatch_unknown_decision_raises(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        with pytest.raises(ValueError, match="decision_id inconnu"):
            dispatch_decision(conn, 99999)


# ---------------------------------------------------------------------
# Filter list_pending par niveau
# ---------------------------------------------------------------------


def test_list_pending_filter_by_level(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        d3 = _insert_dummy_decision(conn, ACTION_REPLAN_LOCAL)
        d4 = _insert_dummy_decision(conn, ACTION_REPLAN_GLOBAL)
        dispatch_decision(conn, d3)
        dispatch_decision(conn, d4)
        l3_only = list_pending(conn, autonomy_level=AUTONOMY_LEVEL_L3)
        l4_only = list_pending(conn, autonomy_level=AUTONOMY_LEVEL_L4)
        all_p = list_pending(conn)
        assert len(l3_only) == 1
        assert len(l4_only) == 1
        assert len(all_p) == 2
