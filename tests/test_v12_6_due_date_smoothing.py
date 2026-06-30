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
    _compute_article_bom_level,
    _compute_latest_start_cpm_minutes,
    _compute_latest_start_horizon_minutes,
    _compute_latest_start_minutes,
    _compute_workstation_queueing_factors,
    _estimate_candidate_duration_min,
    _estimate_candidate_makespan_cpm,
    _get_bom_topo_flag,
    _get_cpm_aware_flag,
    _get_due_date_aware_flag,
    _get_horizon_aware_flag,
    _get_slack_ordering_flag,
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


# --- V12.8 — CPM + Little + BOM topo tests ---


def _enable_cpm_aware(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO parameters (scope, scope_ref, name, value_num) "
        "VALUES (?, ?, ?, ?)",
        ("global", None, "smoothing_cpm_aware", 1.0),
    )


def test_cpm_aware_default_off(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        assert _get_cpm_aware_flag(conn) is False


def test_cpm_aware_enabled(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _enable_cpm_aware(conn)
        assert _get_cpm_aware_flag(conn) is True


def test_slack_ordering_default_off(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        assert _get_slack_ordering_flag(conn) is False


def test_bom_topo_default_off(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        assert _get_bom_topo_flag(conn) is False


def test_queueing_factors_empty_when_no_candidates(tmp_db) -> None:
    """Pas de candidate active → dict vide."""
    with db_session(tmp_db) as conn:
        factors = _compute_workstation_queueing_factors(conn, 10000, 0.95)
        assert factors == {}


def test_queueing_factors_combines_little_and_concurrency(tmp_db) -> None:
    """Le facteur = max(n_competitors, 1/(1-ρ))."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO workstations "
            "(workstation_id, label, sequence_idx) VALUES ('WS-A', 'T', 1)"
        )
        # 3 articles routés tous via WS-A
        for art in ("A", "B", "C"):
            conn.execute(
                "INSERT OR IGNORE INTO articles (article_id, label) VALUES (?, ?)",
                (f"ART-{art}", "T"),
            )
            conn.execute(
                "INSERT INTO routing_operations "
                "(article_id, sequence_idx, workstation_id, unit_time_min) "
                "VALUES (?, 1, 'WS-A', 5.0)",
                (f"ART-{art}",),
            )
            conn.execute(
                "INSERT INTO candidate_orders "
                "(candidate_id, article_id, quantity, status) "
                "VALUES (?, ?, 10, 'candidate')",
                (f"CND-{art}", f"ART-{art}"),
            )
        # horizon très grand → ρ_little ≈ 0, factor = max(n_competitors=3, 1)
        factors = _compute_workstation_queueing_factors(conn, 1_000_000, 0.95)
        assert "WS-A" in factors
        assert factors["WS-A"] >= 3.0


def test_estimate_candidate_makespan_cpm_no_routing(tmp_db) -> None:
    """Sans routing → fallback 960 min."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO articles (article_id, label) VALUES (?, ?)",
            ("ART-NOR", "T"),
        )
        conn.execute(
            "INSERT INTO candidate_orders "
            "(candidate_id, article_id, quantity, status) "
            "VALUES (?, ?, 10, 'candidate')",
            ("CND-NOR", "ART-NOR"),
        )
        m = _estimate_candidate_makespan_cpm(conn, "CND-NOR", {})
        assert m == 960


def test_estimate_candidate_makespan_cpm_scales_with_factor(tmp_db) -> None:
    """Makespan = Σ(unit × qty / capa × factor[ws])."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO workstations "
            "(workstation_id, label, sequence_idx) VALUES ('WS-X', 'T', 1)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO articles (article_id, label) VALUES (?, ?)",
            ("ART-T", "T"),
        )
        conn.execute(
            "INSERT INTO routing_operations "
            "(article_id, sequence_idx, workstation_id, unit_time_min) "
            "VALUES ('ART-T', 1, 'WS-X', 2.0)",
        )
        conn.execute(
            "INSERT INTO candidate_orders "
            "(candidate_id, article_id, quantity, status) "
            "VALUES ('CND-T', 'ART-T', 100, 'candidate')",
        )
        # unit_time=2, qty=100 → naked = 200 min. Avec factor=5 → 1000 min
        m = _estimate_candidate_makespan_cpm(conn, "CND-T", {"WS-X": 5.0})
        assert m == 1000


def test_bom_level_empty_when_no_bom(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        levels = _compute_article_bom_level(conn)
        assert levels == {}


def test_bom_level_assigns_depth_correctly(tmp_db) -> None:
    """ROOT (avec enfant SEMI, SEMI avec enfant LEAF) → root=2, semi=1, leaf=0."""
    with db_session(tmp_db) as conn:
        for a in ("ROOT", "SEMI", "LEAF"):
            conn.execute(
                "INSERT OR IGNORE INTO articles (article_id, label) VALUES (?, ?)",
                (a, "T"),
            )
        conn.execute(
            "INSERT INTO bom_lines (parent_article, child_article, quantity) "
            "VALUES ('ROOT', 'SEMI', 1), ('SEMI', 'LEAF', 1)"
        )
        levels = _compute_article_bom_level(conn)
        assert levels["LEAF"] == 0
        assert levels["SEMI"] == 1
        assert levels["ROOT"] == 2


def test_latest_start_cpm_minutes_subtracts_makespan(tmp_db) -> None:
    """latest_start_cpm = horizon - makespan_cpm."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO workstations "
            "(workstation_id, label, sequence_idx) VALUES ('WS-LS', 'T', 1)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO articles (article_id, label) VALUES (?, ?)",
            ("ART-LS", "T"),
        )
        conn.execute(
            "INSERT INTO routing_operations "
            "(article_id, sequence_idx, workstation_id, unit_time_min) "
            "VALUES ('ART-LS', 1, 'WS-LS', 1.0)",
        )
        conn.execute(
            "INSERT INTO candidate_orders "
            "(candidate_id, article_id, quantity, status) "
            "VALUES ('CND-LS', 'ART-LS', 50, 'candidate')",
        )
        # makespan = 50 × 1 × factor=10 = 500 min ; cap = 10000 - 500 = 9500
        latest = _compute_latest_start_cpm_minutes(
            conn, "CND-LS", 10000, {"WS-LS": 10.0},
        )
        assert latest == 9500
