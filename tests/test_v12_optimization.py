"""Tests V12.2 — Optimisation zone négociable + heuristiques."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from pilotage_flux.cybernetic.optimization import (
    HEURISTIC_ATC,
    HEURISTIC_EDD,
    HEURISTIC_SLACK,
    HEURISTIC_SPT,
    NegotiableZone,
    propose_dynamic_replan,
    resolve_negotiable_zone,
    schedule_heuristic,
)
from pilotage_flux.cybernetic.optimization.heuristics import HEURISTICS
from pilotage_flux.db import db_session


# ---------------------------------------------------------------------
# Heuristiques de séquencement
# ---------------------------------------------------------------------


def _make_3_ofs():
    of_ids = ["A", "B", "C"]
    duration = {"A": 2, "B": 5, "C": 1}
    due = {"A": 10, "B": 7, "C": 15}
    current = {"A": 0, "B": 0, "C": 0}
    return of_ids, duration, due, current


def test_slack_orders_by_slack_ascending() -> None:
    of_ids, dur, due, cur = _make_3_ofs()
    result = schedule_heuristic(
        of_ids, duration_days=dur, due_day=due, current_day=cur,
        freeze_end_day=5, horizon_end_day=20, kind=HEURISTIC_SLACK,
    )
    # B a slack=2 (7-5), A=8 (10-2), C=14 (15-1)
    # Tri SLACK croissant : B avant A avant C
    days = sorted(result.values())
    assert result["B"] == days[0]
    assert result["C"] == days[-1]


def test_edd_orders_by_due_date() -> None:
    of_ids, dur, due, cur = _make_3_ofs()
    result = schedule_heuristic(
        of_ids, duration_days=dur, due_day=due, current_day=cur,
        freeze_end_day=5, horizon_end_day=20, kind=HEURISTIC_EDD,
    )
    # EDD : B (7) < A (10) < C (15)
    assert result["B"] < result["A"] < result["C"]


def test_spt_orders_by_duration() -> None:
    of_ids, dur, due, cur = _make_3_ofs()
    result = schedule_heuristic(
        of_ids, duration_days=dur, due_day=due, current_day=cur,
        freeze_end_day=5, horizon_end_day=20, kind=HEURISTIC_SPT,
    )
    # SPT : C (1) < A (2) < B (5)
    assert result["C"] < result["A"] < result["B"]


def test_atc_combines_urgency_and_duration() -> None:
    of_ids, dur, due, cur = _make_3_ofs()
    result = schedule_heuristic(
        of_ids, duration_days=dur, due_day=due, current_day=cur,
        freeze_end_day=5, horizon_end_day=20, kind=HEURISTIC_ATC,
    )
    # B est urgent (slack 2) ET long (5) → priorité ATC élevée
    # → B passe avant A et C
    assert result["B"] <= result["A"]


def test_heuristic_unknown_kind_raises() -> None:
    with pytest.raises(ValueError, match="inconnue"):
        schedule_heuristic(
            ["A"], duration_days={"A": 1}, due_day={"A": 5},
            current_day={"A": 0}, freeze_end_day=0, horizon_end_day=10,
            kind="bogus",
        )


def test_heuristic_empty_returns_empty() -> None:
    assert schedule_heuristic(
        [], duration_days={}, due_day={}, current_day={},
        freeze_end_day=0, horizon_end_day=10,
    ) == {}


def test_all_heuristics_respect_zone_bounds() -> None:
    of_ids, dur, due, cur = _make_3_ofs()
    for kind in HEURISTICS:
        result = schedule_heuristic(
            of_ids, duration_days=dur, due_day=due, current_day=cur,
            freeze_end_day=5, horizon_end_day=20, kind=kind,
        )
        for day in result.values():
            assert 5 <= day < 20, f"{kind}: jour {day} hors zone"


# ---------------------------------------------------------------------
# Zone resolver
# ---------------------------------------------------------------------


def _seed_meta_and_of(
    conn: sqlite3.Connection, of_id: str,
    article: str, days_from_start: int,
) -> None:
    """Insère un OF planifié à `days_from_start` jours après horizon_start."""
    base = datetime.fromisoformat("2026-07-06")
    planned = (base + timedelta(days=days_from_start)).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO articles (article_id, label) VALUES (?, ?)",
        (article, article),
    )
    conn.execute(
        """
        INSERT INTO manufacturing_orders
            (of_id, article_id, quantity, status, planned_start)
        VALUES (?, ?, 10, 'created', ?)
        """,
        (of_id, article, planned),
    )


def _seed_horizon(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO run_metadata (key, value) VALUES (?, ?)",
        ("horizon_start", "2026-07-06"),
    )


def test_zone_resolver_filters_correctly(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _seed_horizon(conn)
        # OF1 dans freeze (jour 2), OF2 dans zone négociable (jour 10),
        # OF3 au-delà (jour 35)
        _seed_meta_and_of(conn, "OF-FREEZE", "ART-1", 2)
        _seed_meta_and_of(conn, "OF-NEGO", "ART-2", 10)
        _seed_meta_and_of(conn, "OF-LIBRE", "ART-3", 35)

        zone = resolve_negotiable_zone(
            conn, reference_day=0, freeze_window_days=5,
            horizon_forecast_days=28,
        )
        assert zone.freeze_end_day == 5
        assert zone.horizon_end_day == 28
        assert "OF-NEGO" in zone.of_ids_in_zone
        assert "OF-FREEZE" not in zone.of_ids_in_zone
        assert "OF-LIBRE" not in zone.of_ids_in_zone


def test_zone_resolver_skips_closed_ofs(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _seed_horizon(conn)
        _seed_meta_and_of(conn, "OF-1", "ART-1", 10)
        conn.execute(
            "UPDATE manufacturing_orders SET status = 'closed' WHERE of_id = 'OF-1'"
        )
        zone = resolve_negotiable_zone(
            conn, reference_day=0, freeze_window_days=5,
            horizon_forecast_days=28,
        )
        assert "OF-1" not in zone.of_ids_in_zone


def test_zone_empty_when_no_of_in_window(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _seed_horizon(conn)
        _seed_meta_and_of(conn, "OF-OUT", "ART-1", 100)
        zone = resolve_negotiable_zone(
            conn, reference_day=0, freeze_window_days=5,
            horizon_forecast_days=28,
        )
        assert zone.is_empty


def test_zone_width_days_computed() -> None:
    zone = NegotiableZone(
        reference_day=0, freeze_end_day=5, horizon_end_day=28,
    )
    assert zone.width_days == 23


# ---------------------------------------------------------------------
# CP-SAT dynamic
# ---------------------------------------------------------------------


def test_propose_replan_empty_zone(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _seed_horizon(conn)
        zone = resolve_negotiable_zone(
            conn, reference_day=0, freeze_window_days=5,
            horizon_forecast_days=28,
        )
        assert zone.is_empty
        result = propose_dynamic_replan(conn, zone)
        assert result.status == "empty_zone"
        assert result.n_ofs_moved == 0


def test_propose_replan_returns_new_days_within_zone(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _seed_horizon(conn)
        # 5 OFs dans la zone négociable, à différents jours
        for i, day in enumerate([6, 8, 10, 12, 15]):
            _seed_meta_and_of(conn, f"OF-{i}", f"ART-{i}", day)
        zone = resolve_negotiable_zone(
            conn, reference_day=0, freeze_window_days=5,
            horizon_forecast_days=28,
        )
        assert len(zone.of_ids_in_zone) == 5
        result = propose_dynamic_replan(conn, zone, timeout_sec=5.0)
        assert result.status in {"optimal", "feasible", "fallback_slack"}
        # Tous les nouveaux jours doivent être dans [freeze_end, horizon_end[
        for of_id, day in result.new_launch_day_by_of.items():
            assert zone.freeze_end_day <= day < zone.horizon_end_day


def test_propose_replan_indicators_computed(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _seed_horizon(conn)
        for i, day in enumerate([6, 10, 15]):
            _seed_meta_and_of(conn, f"OF-{i}", f"ART-{i}", day)
        zone = resolve_negotiable_zone(
            conn, reference_day=0, freeze_window_days=5,
            horizon_forecast_days=28,
        )
        result = propose_dynamic_replan(conn, zone, timeout_sec=5.0)
        # Indicateurs cohérents
        assert result.n_ofs_moved >= 0
        assert result.max_delta_days >= 0
        assert result.total_delta_days >= result.max_delta_days
        # Cohérence delta vs nouveaux jours
        for of_id, delta in result.deltas.items():
            assert isinstance(delta, int)
