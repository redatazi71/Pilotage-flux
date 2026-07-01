"""V13.E — Tests TOC-aware smoothing (DBR + goulot dynamique).

Vérifie :
- flags smoothing_toc_aware + buffer_days
- identification dynamique du goulot par ratio ρ
- cadencement DBR au débit goulot
- buffer temporel amont
- dossier de faisabilité produit
"""

from __future__ import annotations

import sqlite3

from pilotage_flux.db import db_session
from pilotage_flux.flux.smoothing import (
    _compute_toc_aware_offsets,
    _get_toc_aware_flag,
    _get_toc_buffer_days,
    _identify_bottleneck,
)


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


def _seed_article_multi_ops(conn, article, ops):
    """ops = [(ws_id, unit_time_min), ...]"""
    conn.execute(
        "INSERT OR IGNORE INTO articles (article_id, label) VALUES (?, ?)",
        (article, article),
    )
    for idx, (ws, ut) in enumerate(ops, start=1):
        conn.execute(
            "INSERT INTO routing_operations "
            "(article_id, sequence_idx, workstation_id, unit_time_min) "
            "VALUES (?, ?, ?, ?)",
            (article, idx, ws, ut),
        )


def test_default_flag_is_off(tmp_db):
    with db_session(tmp_db) as conn:
        assert _get_toc_aware_flag(conn) is False


def test_flag_enabled_when_param_set(tmp_db):
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'smoothing_toc_aware', 1.0)"
        )
        assert _get_toc_aware_flag(conn) is True


def test_buffer_days_default_is_2(tmp_db):
    with db_session(tmp_db) as conn:
        assert _get_toc_buffer_days(conn) == 2


def test_buffer_days_clamped(tmp_db):
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'smoothing_toc_buffer_days', 100.0)"
        )
        assert _get_toc_buffer_days(conn) == 10


def test_identify_bottleneck_picks_highest_rho(tmp_db):
    """Le goulot = argmax(charge/capa). WS-Y a capa 0.3 (faible) et
    reçoit toute la charge → ρ le plus haut → bottleneck."""
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        _seed_ws(conn, "WS-X", 0.9)  # capa haute
        _seed_ws(conn, "WS-Y", 0.3)  # capa basse
        _seed_article_multi_ops(conn, "ART", [("WS-X", 2.0), ("WS-Y", 3.0)])
        candidates = [
            {"candidate_id": "C1", "article_id": "ART",
             "qty_in_contract": 50}
        ]
        bottleneck, loads, capa = _identify_bottleneck(
            conn, candidates, horizon_days=10, daily_min=480,
        )
        # WS-Y : load = 150 min, capa = 480×0.3×10 = 1440 → ρ = 0.10
        # WS-X : load = 100 min, capa = 480×0.9×10 = 4320 → ρ = 0.02
        # → WS-Y goulot
        assert bottleneck == "WS-Y"
        assert loads["WS-Y"] == 150.0
        assert loads["WS-X"] == 100.0


def test_toc_places_at_launch_day_before_goulot_slot(tmp_db):
    """Buffer 2j : lancement = goulot_slot - 2, jamais négatif."""
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        _seed_ws(conn, "WS-A", 1.0)
        _seed_ws(conn, "WS-B", 0.5)  # goulot
        _seed_article_multi_ops(conn, "ART", [("WS-A", 1.0), ("WS-B", 4.0)])
        conn.execute(
            "INSERT INTO candidate_orders "
            "(candidate_id, article_id, quantity, status) "
            "VALUES ('C1', 'ART', 30, 'candidate')"
        )
        candidates = [
            {"candidate_id": "C1", "article_id": "ART",
             "qty_in_contract": 30}
        ]
        offsets, feas = _compute_toc_aware_offsets(
            conn, candidates,
            horizon_min=10 * 1440, target_saturation=0.85,
            buffer_days=2,
        )
        # Goulot_slot = 0 (WS-B a de la capacité au j0), launch = max(0, 0-2) = 0
        assert offsets["C1"] == 0
        assert feas["C1"]["bottleneck_ws"] == "WS-B"
        assert feas["C1"]["goulot_slot_day"] == 0
        assert feas["C1"]["launch_day"] == 0
        assert feas["C1"]["buffer_days"] == 2


def test_toc_buffer_delays_launch_when_goulot_slot_later(tmp_db):
    """Quand le goulot est saturé au j0-j2, goulot_slot = j3 → launch = j1."""
    with db_session(tmp_db) as conn:
        _seed_calendar(conn, daily_min=480)
        _seed_ws(conn, "WS-A", 1.0)
        _seed_ws(conn, "WS-BG", 1.0)  # goulot, budget 408min/jour
        _seed_article_multi_ops(conn, "ART",
                                  [("WS-A", 1.0), ("WS-BG", 5.0)])
        # Chaque candidate qty 80 → charge WS-BG = 400 min ≈ budget
        # 4 candidates → j0, j1, j2, j3 sur goulot
        # C1 → goulot j0, launch max(0, 0-2)=0
        # C4 → goulot j3, launch max(0, 3-2)=1
        candidates = []
        for i in range(4):
            cid = f"C{i}"
            conn.execute(
                "INSERT INTO candidate_orders "
                "(candidate_id, article_id, quantity, status) "
                "VALUES (?, 'ART', 80, 'candidate')",
                (cid,),
            )
            candidates.append(
                {"candidate_id": cid, "article_id": "ART",
                 "qty_in_contract": 80}
            )
        offsets, feas = _compute_toc_aware_offsets(
            conn, candidates,
            horizon_min=10 * 1440, target_saturation=0.85,
            buffer_days=2,
        )
        # Chaque candidate a un slot goulot différent (échelonné j0..j3)
        goulot_slots = sorted([feas[c["candidate_id"]]["goulot_slot_day"]
                                for c in candidates])
        assert goulot_slots == [0, 1, 2, 3]
        # Le buffer 2j décale les launches
        launches = sorted([feas[c["candidate_id"]]["launch_day"]
                            for c in candidates])
        # Launches attendus : max(0,0-2)=0, max(0,1-2)=0,
        # max(0,2-2)=0, max(0,3-2)=1
        assert launches == [0, 0, 0, 1]


def test_toc_feasibility_report_has_all_fields(tmp_db):
    """Le dossier de faisabilité produit doit contenir les métadonnées
    attendues par la simulation cybernétique."""
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        _seed_ws(conn, "WS-X", 0.5)
        _seed_article_multi_ops(conn, "ART", [("WS-X", 2.0)])
        conn.execute(
            "INSERT INTO candidate_orders "
            "(candidate_id, article_id, quantity, status) "
            "VALUES ('C1', 'ART', 50, 'candidate')"
        )
        candidates = [
            {"candidate_id": "C1", "article_id": "ART",
             "qty_in_contract": 50}
        ]
        _, feas = _compute_toc_aware_offsets(
            conn, candidates,
            horizon_min=10 * 1440, target_saturation=0.85, buffer_days=2,
        )
        expected_keys = {
            "bottleneck_ws", "rho_bottleneck_run", "goulot_load_min",
            "goulot_slot_day", "launch_day", "buffer_days",
            "charge_total_min", "takt_min_per_unit_target",
            "wip_predicted", "feasible",
        }
        assert set(feas["C1"].keys()) == expected_keys
        assert feas["C1"]["feasible"] == 1
