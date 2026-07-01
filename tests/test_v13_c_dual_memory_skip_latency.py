"""V13.C — Tests du raccourci mémoire (skip latency).

Quand le flag `enable_dual_memory_skip_latency` est activé et qu'une
recette apprise existe pour un deviation_kind (≥ min_recurrence retenues
avec outcome != failure), `evaluate_dual_tolerance` court-circuite
l'analyse et applique directement l'action_level appris.
"""

from __future__ import annotations

from pathlib import Path

from pilotage_flux.db import db_session
from pilotage_flux.events_v3 import (
    evaluate_dual_tolerance,
    try_memory_shortcut,
)


def _seed_deviation(conn, deviation_kind: str, score: float = 0.5) -> int:
    """Crée une déviation minimale (sans expected_event) et renvoie son id."""
    cur = conn.execute(
        """
        INSERT INTO event_deviations
            (candidate_id, deviation_kind, score, is_absorbed, detected_at)
        VALUES (NULL, ?, ?, 0, datetime('now'))
        """,
        (deviation_kind, score),
    )
    return int(cur.lastrowid)


def _seed_retained_recipe(
    conn, deviation_kind: str, action_level: str,
    outcome: str = "success",
) -> None:
    conn.execute(
        """
        INSERT INTO memory_recipes
            (of_id, candidate_id, deviation_signature, deviation_kind,
             action_level, outcome, is_retained, score_combined)
        VALUES (NULL, NULL, ?, ?, ?, ?, 1, 0.9)
        """,
        (f"{deviation_kind}|-|{action_level}", deviation_kind,
         action_level, outcome),
    )


def _enable_skip_latency(conn) -> None:
    conn.execute(
        "INSERT INTO parameters (scope, scope_ref, name, value_num) "
        "VALUES ('global', NULL, 'enable_dual_memory_skip_latency', 1.0)"
    )


def test_shortcut_returns_none_when_no_recipes(tmp_db: Path):
    with db_session(tmp_db) as conn:
        dev_id = _seed_deviation(conn, "time_delta")
        assert try_memory_shortcut(conn, dev_id) is None


def test_shortcut_returns_none_below_min_recurrence(tmp_db: Path):
    with db_session(tmp_db) as conn:
        # 1 seule recette → défaut min_recurrence=2 → None
        _seed_retained_recipe(conn, "time_delta", "correct_local")
        dev_id = _seed_deviation(conn, "time_delta")
        assert try_memory_shortcut(conn, dev_id) is None


def test_shortcut_returns_action_when_recurrence_met(tmp_db: Path):
    with db_session(tmp_db) as conn:
        _seed_retained_recipe(conn, "time_delta", "correct_local")
        _seed_retained_recipe(conn, "time_delta", "correct_local")
        dev_id = _seed_deviation(conn, "time_delta")
        assert try_memory_shortcut(conn, dev_id) == "correct_local"


def test_shortcut_ignores_failure_outcomes(tmp_db: Path):
    with db_session(tmp_db) as conn:
        _seed_retained_recipe(conn, "time_delta", "escalate",
                              outcome="failure")
        _seed_retained_recipe(conn, "time_delta", "escalate",
                              outcome="failure")
        dev_id = _seed_deviation(conn, "time_delta")
        assert try_memory_shortcut(conn, dev_id) is None


def test_shortcut_picks_majority_action(tmp_db: Path):
    """3× correct_local vs 2× replan_local → correct_local gagne."""
    with db_session(tmp_db) as conn:
        for _ in range(3):
            _seed_retained_recipe(conn, "qty_delta", "correct_local")
        for _ in range(2):
            _seed_retained_recipe(conn, "qty_delta", "replan_local")
        dev_id = _seed_deviation(conn, "qty_delta")
        assert try_memory_shortcut(conn, dev_id) == "correct_local"


def test_evaluate_ignores_memory_when_flag_off(tmp_db: Path):
    """Flag off (défaut) → tolérance normale, source='tolerance'."""
    with db_session(tmp_db) as conn:
        _seed_retained_recipe(conn, "time_delta", "escalate")
        _seed_retained_recipe(conn, "time_delta", "escalate")
        dev_id = _seed_deviation(conn, "time_delta", score=0.1)
        d = evaluate_dual_tolerance(conn, dev_id)
        assert d.source == "tolerance"
        # score 0.1 → inform (< tolerance_threshold_watch=0.20)
        assert d.action_level == "inform"


def test_evaluate_uses_shortcut_when_flag_on(tmp_db: Path):
    """Flag on + ≥2 recipes → source='memory_shortcut', action apprise."""
    with db_session(tmp_db) as conn:
        _enable_skip_latency(conn)
        _seed_retained_recipe(conn, "time_delta", "escalate")
        _seed_retained_recipe(conn, "time_delta", "escalate")
        # score 0.1 → route normale = inform, mais mémoire dit escalate
        dev_id = _seed_deviation(conn, "time_delta", score=0.1)
        d = evaluate_dual_tolerance(conn, dev_id)
        assert d.source == "memory_shortcut"
        assert d.action_level == "escalate"
        assert d.latency_minutes == 0
        assert d.triggered_at is not None  # pas d'attente


def test_evaluate_absorbed_deviation_ignores_shortcut(tmp_db: Path):
    """Déviation absorbée CPM → inform via chemin normal, même avec flag."""
    with db_session(tmp_db) as conn:
        _enable_skip_latency(conn)
        _seed_retained_recipe(conn, "time_delta", "escalate")
        _seed_retained_recipe(conn, "time_delta", "escalate")
        cur = conn.execute(
            """
            INSERT INTO event_deviations
                (candidate_id, deviation_kind, score, is_absorbed, detected_at)
            VALUES (NULL, 'time_delta', 0.5, 1, datetime('now'))
            """,
        )
        dev_id = int(cur.lastrowid)
        d = evaluate_dual_tolerance(conn, dev_id)
        assert d.source == "tolerance"
        assert d.action_level == "inform"


def test_shortcut_custom_min_recurrence(tmp_db: Path):
    """min_recurrence paramétrable via 'memory_shortcut_min_recurrence'."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, "
            "'memory_shortcut_min_recurrence', 1.0)"
        )
        _seed_retained_recipe(conn, "time_delta", "watch")
        dev_id = _seed_deviation(conn, "time_delta")
        assert try_memory_shortcut(conn, dev_id) == "watch"


def test_evaluate_idempotent_with_shortcut(tmp_db: Path):
    """2ᵉ appel → renvoie la même décision, sans re-créer."""
    with db_session(tmp_db) as conn:
        _enable_skip_latency(conn)
        _seed_retained_recipe(conn, "time_delta", "correct_local")
        _seed_retained_recipe(conn, "time_delta", "correct_local")
        dev_id = _seed_deviation(conn, "time_delta", score=0.5)
        d1 = evaluate_dual_tolerance(conn, dev_id)
        d2 = evaluate_dual_tolerance(conn, dev_id)
        assert d1.decision_id == d2.decision_id
        assert d1.source == d2.source == "memory_shortcut"
