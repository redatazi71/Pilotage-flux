"""V13.D — Tests capacity-aware smoothing.

Vérifie que le drapeau `smoothing_capacity_aware = 1` remplace le
smoothing linéaire V1.4 par un placement earliest-first respectant
le budget par WS × jour à saturation cible.
"""

from __future__ import annotations

import sqlite3

from pilotage_flux.db import db_session
from pilotage_flux.flux.smoothing import (
    _candidate_ws_loads,
    _compute_capacity_aware_offsets,
    _get_capacity_aware_flag,
    _get_target_saturation,
)


def _seed_ws(conn: sqlite3.Connection, ws_id: str, capa: float) -> None:
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


def _seed_calendar(conn: sqlite3.Connection, daily_min: int = 480) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO calendars "
        "(calendar_id, label, daily_minutes) VALUES (?, ?, ?)",
        ("CAL-DEFAULT", "test", daily_min),
    )


def _seed_article_routing(
    conn: sqlite3.Connection, article: str, ws_id: str, unit_time: float,
) -> None:
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


def test_default_flag_is_off(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        assert _get_capacity_aware_flag(conn) is False


def test_flag_enabled_when_param_set(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'smoothing_capacity_aware', 1.0)"
        )
        assert _get_capacity_aware_flag(conn) is True


def test_target_saturation_default(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        assert _get_target_saturation(conn) == 0.85


def test_target_saturation_clamped(tmp_db) -> None:
    """Values are clamped to [0.30, 0.99]."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'smoothing_target_saturation', 1.5)"
        )
        assert _get_target_saturation(conn) == 0.99


def test_candidate_ws_loads_uses_machine_time(tmp_db) -> None:
    """Charge = qty × unit_time (temps machine brut, sans division capa)."""
    with db_session(tmp_db) as conn:
        _seed_ws(conn, "WS-1", 0.5)
        _seed_article_routing(conn, "ART-A", "WS-1", 3.0)
        loads = _candidate_ws_loads(conn, "ART-A", qty=10)
        assert loads == [("WS-1", 30.0)]  # 10 × 3.0 = 30 min


def test_capacity_aware_places_first_candidate_at_day_zero(tmp_db) -> None:
    """Le premier candidate doit se poser au jour 0 s'il rentre dans
    le budget."""
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        _seed_ws(conn, "WS-A", 1.0)
        _seed_article_routing(conn, "ART-A", "WS-A", 2.0)
        conn.execute(
            "INSERT INTO candidate_orders "
            "(candidate_id, article_id, quantity, status) "
            "VALUES ('C1', 'ART-A', 10, 'candidate')"
        )
        candidates = [
            {"candidate_id": "C1", "article_id": "ART-A",
             "qty_in_contract": 10}
        ]
        offsets = _compute_capacity_aware_offsets(
            conn, candidates,
            horizon_min=60 * 1440, target_saturation=0.85,
        )
        assert offsets["C1"] == 0


def test_capacity_aware_spreads_candidates_across_days(tmp_db) -> None:
    """Plusieurs candidates qui saturent un WS doivent s'étaler sur
    jours consécutifs."""
    with db_session(tmp_db) as conn:
        _seed_calendar(conn, daily_min=480)
        _seed_ws(conn, "WS-B", 1.0)
        _seed_article_routing(conn, "ART-B", "WS-B", 5.0)
        candidates = []
        for i in range(5):
            cid = f"C{i}"
            conn.execute(
                "INSERT INTO candidate_orders "
                "(candidate_id, article_id, quantity, status) "
                "VALUES (?, 'ART-B', 80, 'candidate')",
                (cid,),
            )
            candidates.append(
                {"candidate_id": cid, "article_id": "ART-B",
                 "qty_in_contract": 80}
            )
        # Chaque candidate : 80 × 5 = 400 min charge sur WS-B
        # Budget WS-B/jour = 480 × 1.0 × 0.85 = 408 min → 1 candidate/jour
        offsets = _compute_capacity_aware_offsets(
            conn, candidates, horizon_min=10 * 1440, target_saturation=0.85,
        )
        # Attendu : chaque candidate à un jour différent, ordre croissant
        days = sorted([offsets[c["candidate_id"]] // 1440
                        for c in candidates])
        assert days == [0, 1, 2, 3, 4]


def test_capacity_aware_respects_target_saturation(tmp_db) -> None:
    """Charge cumulée par WS × jour reste ≤ budget × target."""
    with db_session(tmp_db) as conn:
        _seed_calendar(conn, daily_min=480)
        _seed_ws(conn, "WS-C", 1.0)
        _seed_article_routing(conn, "ART-C", "WS-C", 4.0)
        # 3 candidates 50 unités : 3 × 200 = 600 min charge, budget/jour
        # = 480 × 0.85 = 408 min → doit tenir en 2 jours (200 + 200 = 400
        # < 408, ok ; le 3e va au j1 : 200 < 408, ok)
        candidates = []
        for i in range(3):
            cid = f"C{i}"
            conn.execute(
                "INSERT INTO candidate_orders "
                "(candidate_id, article_id, quantity, status) "
                "VALUES (?, 'ART-C', 50, 'candidate')",
                (cid,),
            )
            candidates.append(
                {"candidate_id": cid, "article_id": "ART-C",
                 "qty_in_contract": 50}
            )
        offsets = _compute_capacity_aware_offsets(
            conn, candidates, horizon_min=10 * 1440, target_saturation=0.85,
        )
        days = sorted([offsets[c["candidate_id"]] // 1440
                        for c in candidates])
        # 2 au j0 (2 × 200 = 400 < 408), 1 au j1
        assert days == [0, 0, 1]


def test_capacity_aware_falls_back_gracefully_when_infeasible(
    tmp_db,
) -> None:
    """Si un candidate a une charge > budget quotidien, place au jour
    minimisant le surplus (au plus tôt en cas d'égalité)."""
    with db_session(tmp_db) as conn:
        _seed_calendar(conn, daily_min=480)
        _seed_ws(conn, "WS-D", 0.2)  # capa très basse
        _seed_article_routing(conn, "ART-D", "WS-D", 5.0)
        # 1 candidate qty 100 : 500 min charge. Budget/jour = 480 × 0.2
        # × 0.85 = 82 min → jamais 500 min ne fitte.
        conn.execute(
            "INSERT INTO candidate_orders "
            "(candidate_id, article_id, quantity, status) "
            "VALUES ('CX', 'ART-D', 100, 'candidate')"
        )
        candidates = [
            {"candidate_id": "CX", "article_id": "ART-D",
             "qty_in_contract": 100}
        ]
        offsets = _compute_capacity_aware_offsets(
            conn, candidates, horizon_min=5 * 1440, target_saturation=0.85,
        )
        # Impossible mais on doit poser au jour 0 (minimal excess et
        # earliest en cas d'égalité — tous jours ont même excess quand
        # les jours sont tous vides)
        assert offsets["CX"] == 0
