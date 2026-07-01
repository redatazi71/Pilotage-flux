"""V13.G — Tests MES DBR-aware runner.

Vérifie :
- flags mes_dbr_aware + target_saturation
- _identify_mes_dbr_bottleneck picks WS with highest effective load
- HazardState porte dbr_bottleneck_ws + dbr_budget_min
"""

from __future__ import annotations

from pilotage_flux.comparative.runner import (
    HazardState,
    _get_mes_dbr_aware_flag,
    _get_mes_dbr_target_saturation,
    _identify_mes_dbr_bottleneck,
)
from pilotage_flux.db import db_session


def _seed_ws(conn, ws_id, capa):
    conn.execute(
        "INSERT OR IGNORE INTO workstations (workstation_id, label, "
        "sequence_idx) VALUES (?, ?, 1)",
        (ws_id, ws_id),
    )
    conn.execute(
        "INSERT INTO parameters (scope, scope_ref, name, value_num) "
        "VALUES ('workstation', ?, 'capacity_factor', ?)",
        (ws_id, capa),
    )


def _seed_calendar(conn, daily_min=480):
    conn.execute(
        "INSERT OR IGNORE INTO calendars "
        "(calendar_id, label, daily_minutes) VALUES (?, ?, ?)",
        ("CAL-DEFAULT", "test", daily_min),
    )


def _seed_article_op(conn, article, ws_id, unit_time):
    conn.execute(
        "INSERT OR IGNORE INTO articles (article_id, label) VALUES (?, ?)",
        (article, article),
    )
    conn.execute(
        "INSERT INTO routing_operations "
        "(article_id, sequence_idx, workstation_id, unit_time_min) "
        "VALUES (?, 1, ?, ?)",
        (article, ws_id, unit_time),
    )


def _seed_candidate(conn, cid, article, qty):
    conn.execute(
        "INSERT INTO candidate_orders "
        "(candidate_id, article_id, quantity, status) "
        "VALUES (?, ?, ?, 'candidate')",
        (cid, article, qty),
    )


def test_default_flag_is_off(tmp_db):
    with db_session(tmp_db) as conn:
        assert _get_mes_dbr_aware_flag(conn) is False


def test_flag_enabled_when_param_set(tmp_db):
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'mes_dbr_aware', 1.0)"
        )
        assert _get_mes_dbr_aware_flag(conn) is True


def test_target_saturation_default(tmp_db):
    with db_session(tmp_db) as conn:
        assert _get_mes_dbr_target_saturation(conn) == 0.85


def test_target_saturation_clamped(tmp_db):
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'mes_dbr_target_saturation', 2.0)"
        )
        assert _get_mes_dbr_target_saturation(conn) == 0.99


def test_identify_bottleneck_none_without_candidates(tmp_db):
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        _seed_ws(conn, "WS-A", 1.0)
        ws, budget = _identify_mes_dbr_bottleneck(conn, 0.85)
        assert ws is None
        assert budget == 0.0


def test_identify_bottleneck_picks_highest_effective_load(tmp_db):
    """WS-Y capa 0.3 reçoit toute la charge → ρ le plus haut."""
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        _seed_ws(conn, "WS-X", 0.9)
        _seed_ws(conn, "WS-Y", 0.3)
        _seed_article_op(conn, "ART-A", "WS-X", 1.0)  # sur WS-X
        _seed_article_op(conn, "ART-B", "WS-Y", 3.0)  # sur WS-Y
        _seed_candidate(conn, "C1", "ART-A", 50)  # charge WS-X = 50
        _seed_candidate(conn, "C2", "ART-B", 50)  # charge WS-Y = 150
        ws, budget = _identify_mes_dbr_bottleneck(conn, 0.85)
        # load_eff WS-X = 50 / 0.9 = 55.6
        # load_eff WS-Y = 150 / 0.3 = 500 → goulot
        assert ws == "WS-Y"
        # budget = daily_min × target_sat = 480 × 0.85 = 408 (base capa
        # est déjà factorisée dans op_dur côté exécution)
        assert 405 < budget < 410


def test_hazard_state_has_dbr_fields():
    state = HazardState()
    assert state.dbr_bottleneck_ws is None
    assert state.dbr_budget_min == 0.0
