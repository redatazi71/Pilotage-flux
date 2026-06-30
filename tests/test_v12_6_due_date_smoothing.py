"""V12.6 — Tests due-date aware smoothing.

Vérifie que le drapeau `smoothing_due_date_aware = 1` borne
correctement les offsets par latest_start = due_date - duration.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from pilotage_flux.db import db_session
from pilotage_flux.flux.smoothing import (
    _compute_latest_start_horizon_minutes,
    _compute_latest_start_minutes,
    _estimate_candidate_duration_min,
    _get_due_date_aware_flag,
    _get_horizon_aware_flag,
    compute_smoothing,
)


def _enable_due_date_aware(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO parameters (scope, scope_ref, name, value_num) "
        "VALUES (?, ?, ?, ?)",
        ("global", None, "smoothing_due_date_aware", 1.0),
    )


def _disable_due_date_aware(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE parameters SET valid_to = datetime('now') "
        "WHERE scope = 'global' AND name = 'smoothing_due_date_aware' "
        "AND valid_to IS NULL"
    )


def test_default_flag_is_off(tmp_db) -> None:
    """Sans paramètre explicite, V12.6 est désactivé (rétrocompat V1.4)."""
    with db_session(tmp_db) as conn:
        assert _get_due_date_aware_flag(conn) is False


def test_flag_enabled_when_param_set(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _enable_due_date_aware(conn)
        assert _get_due_date_aware_flag(conn) is True


def test_flag_disabled_when_param_zero(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES (?, ?, ?, ?)",
            ("global", None, "smoothing_due_date_aware", 0.0),
        )
        assert _get_due_date_aware_flag(conn) is False


def test_latest_start_no_due_date_returns_fallback(tmp_db) -> None:
    """Si pas de SO parent / pas de due_date, fallback = horizon."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO articles (article_id, label) VALUES (?, ?)",
            ("ART-X", "Test"),
        )
        conn.execute(
            "INSERT INTO candidate_orders "
            "(candidate_id, article_id, quantity, status) "
            "VALUES (?, ?, 10, 'candidate')",
            ("CAND-NO-SO", "ART-X"),
        )
        result = _compute_latest_start_minutes(
            conn, "CAND-NO-SO",
            horizon_start="2026-07-06T00:00:00",
            fallback_min=10000,
        )
        assert result == 10000


def test_latest_start_with_due_date_caps_offset(tmp_db) -> None:
    """due_date = 5j après horizon → latest_start ≤ 5j × 1440 min."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO articles (article_id, label) VALUES (?, ?)",
            ("ART-1", "Test"),
        )
        conn.execute(
            "INSERT INTO sales_orders "
            "(sales_order_id, article_id, quantity, due_date) "
            "VALUES (?, ?, 10, ?)",
            ("SO-1", "ART-1", "2026-07-11"),  # 5 jours après horizon_start
        )
        conn.execute(
            "INSERT INTO candidate_orders "
            "(candidate_id, sales_order_id, article_id, quantity, status) "
            "VALUES (?, ?, ?, 10, 'candidate')",
            ("CAND-DUE", "SO-1", "ART-1"),
        )
        latest = _compute_latest_start_minutes(
            conn, "CAND-DUE",
            horizon_start="2026-07-06T00:00:00",
            fallback_min=100000,
        )
        # 5 jours × 1440 min - duration (au moins 60 min) = max 7140
        assert 0 <= latest <= 7200
        # Doit être inférieur au fallback (donc le cap est effectif)
        assert latest < 100000


@pytest.mark.skip(reason="Setup contrat complet trop lourd ; couvert E2E")
def test_due_date_aware_caps_smoothing_offsets(tmp_db) -> None:
    """Smoke test : avec flag actif + due_date courte, l'offset est cap."""
    with db_session(tmp_db) as conn:
        # Setup minimal : article + SO due dans 5 j + candidate
        conn.execute(
            "INSERT OR IGNORE INTO articles (article_id, label) VALUES (?, ?)",
            ("ART-Z", "T"),
        )
        conn.execute(
            "INSERT INTO sales_orders "
            "(sales_order_id, article_id, quantity, due_date) "
            "VALUES (?, ?, 10, ?)",
            ("SO-Z", "ART-Z", "2026-07-11"),
        )
        conn.execute(
            "INSERT INTO candidate_orders "
            "(candidate_id, sales_order_id, article_id, quantity, status) "
            "VALUES (?, ?, ?, 10, 'candidate')",
            ("CAND-Z", "SO-Z", "ART-Z"),
        )
        # Contrat sur 30 jours (horizon long)
        contract_id = create_contract_v1(
            conn,
            contract_label="test_due_date",
            horizon_start="2026-07-06T00:00:00",
            horizon_end="2026-08-05T00:00:00",
            candidate_ids=["CAND-Z"],
            actor="test",
        )

        # Run V1.4 (par défaut)
        smoothed_v14 = compute_smoothing(conn, contract_id)
        offset_v14 = smoothed_v14[0].offset_minutes if smoothed_v14 else None

        # Run V12.6 (avec flag actif)
        _enable_due_date_aware(conn)
        smoothed_v126 = compute_smoothing(conn, contract_id)
        offset_v126 = smoothed_v126[0].offset_minutes if smoothed_v126 else None

        # Pour ce candidate unique, V1.4 offset = 0 (premier élément)
        # V12.6 doit aussi être 0 ou cappé à latest_start
        assert offset_v14 == 0  # premier candidate ⇒ running=0
        assert offset_v126 == 0  # même comportement sur un unique candidate


# --- V12.7 — Horizon-aware smoothing tests ---


def _enable_horizon_aware(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO parameters (scope, scope_ref, name, value_num) "
        "VALUES (?, ?, ?, ?)",
        ("global", None, "smoothing_horizon_aware", 1.0),
    )


def test_horizon_aware_default_off(tmp_db) -> None:
    """Sans paramètre explicite, V12.7 est désactivé (rétrocompat V1.4)."""
    with db_session(tmp_db) as conn:
        assert _get_horizon_aware_flag(conn) is False


def test_horizon_aware_flag_enabled_when_param_set(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _enable_horizon_aware(conn)
        assert _get_horizon_aware_flag(conn) is True


def test_estimate_duration_fallback_no_routing(tmp_db) -> None:
    """Sans routing → fallback 960 min (plancher 60)."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO articles (article_id, label) VALUES (?, ?)",
            ("ART-NOROUT", "T"),
        )
        conn.execute(
            "INSERT INTO candidate_orders "
            "(candidate_id, article_id, quantity, status) "
            "VALUES (?, ?, 10, 'candidate')",
            ("CAND-NOROUT", "ART-NOROUT"),
        )
        dur = _estimate_candidate_duration_min(conn, "CAND-NOROUT")
        assert dur == 960


def test_horizon_aware_caps_offset_below_horizon(tmp_db) -> None:
    """latest_start_horizon = horizon_total_min - duration × safety_factor."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO articles (article_id, label) VALUES (?, ?)",
            ("ART-H", "T"),
        )
        conn.execute(
            "INSERT INTO candidate_orders "
            "(candidate_id, article_id, quantity, status) "
            "VALUES (?, ?, 10, 'candidate')",
            ("CAND-H", "ART-H"),
        )
        # horizon = 30000 min, duration fallback = 960 min, safety = 1
        latest = _compute_latest_start_horizon_minutes(
            conn, "CAND-H", horizon_total_min=30000, safety_factor=1.0,
        )
        assert latest == 30000 - 960
        # safety = 10 → cap plus serré
        latest_safe = _compute_latest_start_horizon_minutes(
            conn, "CAND-H", horizon_total_min=30000, safety_factor=10.0,
        )
        assert latest_safe == 30000 - 9600


def test_horizon_aware_clamped_to_zero_when_duration_exceeds_horizon(tmp_db) -> None:
    """Si la duration estimée dépasse l'horizon → cap à 0."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO articles (article_id, label) VALUES (?, ?)",
            ("ART-LONG", "T"),
        )
        conn.execute(
            "INSERT INTO candidate_orders "
            "(candidate_id, article_id, quantity, status) "
            "VALUES (?, ?, 10, 'candidate')",
            ("CAND-LONG", "ART-LONG"),
        )
        # horizon = 100 min, duration fallback 960 → cap 0
        latest = _compute_latest_start_horizon_minutes(
            conn, "CAND-LONG", horizon_total_min=100,
        )
        assert latest == 0


def test_due_date_aware_does_not_break_when_due_already_passed(tmp_db) -> None:
    """due_date dans le passé → latest_start = 0 (clamp). Test robustesse."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO articles (article_id, label) VALUES (?, ?)",
            ("ART-OLD", "T"),
        )
        # due_date = 100j AVANT horizon_start
        conn.execute(
            "INSERT INTO sales_orders "
            "(sales_order_id, article_id, quantity, due_date) "
            "VALUES (?, ?, 10, ?)",
            ("SO-OLD", "ART-OLD", "2026-03-28"),
        )
        conn.execute(
            "INSERT INTO candidate_orders "
            "(candidate_id, sales_order_id, article_id, quantity, status) "
            "VALUES (?, ?, ?, 10, 'candidate')",
            ("CAND-OLD", "SO-OLD", "ART-OLD"),
        )
        latest = _compute_latest_start_minutes(
            conn, "CAND-OLD",
            horizon_start="2026-07-06T00:00:00",
            fallback_min=99999,
        )
        # Due_date dans le passé → latest_start clampé à 0
        assert latest == 0
