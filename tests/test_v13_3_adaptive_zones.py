"""V13.3 — Zones adaptatives par nervosité tests."""

from __future__ import annotations

from pilotage_flux.cybernetic.optimization.zone_resolver import (
    compute_adaptive_freeze_window,
)
from pilotage_flux.db import db_session


def test_adaptive_window_returns_base_without_metadata(tmp_db) -> None:
    """Sans run_metadata ni gate_decisions, renvoie base_window_days."""
    with db_session(tmp_db) as conn:
        w = compute_adaptive_freeze_window(conn, base_window_days=5)
        assert w == 5


def test_adaptive_window_contracts_under_high_nervousness(tmp_db) -> None:
    """Beaucoup de replans → fenêtre contractée."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO run_metadata (key, value) "
            "VALUES ('horizon_start', '2026-07-06')"
        )
        # 10 replans sur 10 jours = nervosité 1.0 (> 0.30)
        for _ in range(10):
            conn.execute(
                "INSERT INTO gate_decisions "
                "(gate, subject_type, subject_id, decision, rule_ref, explanation) "
                "VALUES ('P1', 'so', 'SO-1', 'REPLAN', 'r', '')"
            )
        # Forge created_at à 10 jours
        conn.execute(
            "UPDATE gate_decisions SET at_time = '2026-07-16 12:00:00'"
        )
        w = compute_adaptive_freeze_window(conn, base_window_days=5)
        # 5 × 0.5 = 2.5 → 3 après round, mais on clamp à max(1, ...)
        assert 1 <= w <= 5
        assert w < 5  # contracté


def test_adaptive_window_expands_under_low_nervousness(tmp_db) -> None:
    """Peu de replans → fenêtre étendue."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO run_metadata (key, value) "
            "VALUES ('horizon_start', '2026-07-06')"
        )
        # 1 replan sur 20 jours = nervosité 0.05 (< 0.10)
        conn.execute(
            "INSERT INTO gate_decisions "
            "(gate, subject_type, subject_id, decision, rule_ref, explanation) "
            "VALUES ('P1', 'so', 'SO-1', 'REPLAN', 'r', '')"
        )
        conn.execute(
            "UPDATE gate_decisions SET at_time = '2026-07-26 12:00:00'"
        )
        w = compute_adaptive_freeze_window(conn, base_window_days=5)
        # 5 × 1.5 = 7.5 → 8
        assert w > 5
        assert w <= 10  # plafond 2 × base


def test_adaptive_window_neutral_zone(tmp_db) -> None:
    """Nervosité moyenne → window inchangée."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO run_metadata (key, value) "
            "VALUES ('horizon_start', '2026-07-06')"
        )
        # 2 replans sur 10 jours = 0.20 (entre 0.10 et 0.30)
        for _ in range(2):
            conn.execute(
                "INSERT INTO gate_decisions "
                "(gate, subject_type, subject_id, decision, rule_ref, explanation) "
                "VALUES ('P1', 'so', 'SO-1', 'REPLAN', 'r', '')"
            )
        conn.execute(
            "UPDATE gate_decisions SET at_time = '2026-07-16 12:00:00'"
        )
        w = compute_adaptive_freeze_window(conn, base_window_days=5)
        assert w == 5  # inchangé


def test_adaptive_window_respects_floor_and_cap(tmp_db) -> None:
    """Window ∈ [1, 2 × base]."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO run_metadata (key, value) "
            "VALUES ('horizon_start', '2026-07-06')"
        )
        # Force compression extrême
        for _ in range(100):
            conn.execute(
                "INSERT INTO gate_decisions "
                "(gate, subject_type, subject_id, decision, rule_ref, explanation) "
                "VALUES ('P1', 'so', 'SO-1', 'REPLAN', 'r', '')"
            )
        conn.execute(
            "UPDATE gate_decisions SET at_time = '2026-07-07 12:00:00'"
        )
        w = compute_adaptive_freeze_window(conn, base_window_days=5,
                                            contraction_factor=0.01)
        assert w >= 1  # plancher
