"""V13.F — Tests lot-sizing takt-minimal.

Vérifie :
- mode par défaut = off (aucun split, minimise OFs)
- compute_min_splits : renvoie 1 si charge tient dans budget,
  ceil(charge/budget) sinon
- split_candidates_for_takt : crée sub-candidates seulement quand
  nécessaire, préserve la quantité totale
"""

from __future__ import annotations

import sqlite3

from pilotage_flux.db import db_session
from pilotage_flux.flux.lot_sizing import (
    _get_lot_sizing_mode,
    compute_min_splits,
    split_candidates_for_takt,
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


def test_default_mode_is_off(tmp_db):
    with db_session(tmp_db) as conn:
        assert _get_lot_sizing_mode(conn) == "off"


def test_mode_takt_minimal_when_flag_set(tmp_db):
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'smoothing_lot_sizing', 1.0)"
        )
        assert _get_lot_sizing_mode(conn) == "takt_minimal"


def test_min_splits_returns_1_when_no_bottleneck(tmp_db):
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        assert compute_min_splits(conn, "ART-X", 100, None) == 1


def test_min_splits_returns_1_when_charge_fits(tmp_db):
    """Charge 200 min, budget 408 min → tient en 1 slot → 1 OF."""
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        _seed_ws(conn, "WS-G", 1.0)
        _seed_article_op(conn, "ART", "WS-G", 2.0)
        # 100 × 2 = 200 min ; budget = 480 × 1.0 × 0.85 = 408 → 1
        assert compute_min_splits(
            conn, "ART", 100, "WS-G", target_saturation=0.85,
        ) == 1


def test_min_splits_stays_1_within_tolerance(tmp_db):
    """Charge 800 min ≤ 2 × 408 → pas de split (dégradation gracieuse
    V13.D/E gère). Objectif QCDS : min OFs."""
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        _seed_ws(conn, "WS-G", 1.0)
        _seed_article_op(conn, "ART", "WS-G", 4.0)
        # 200 × 4 = 800 min ; seuil = 2 × 408 = 816 → tient
        assert compute_min_splits(
            conn, "ART", 200, "WS-G", target_saturation=0.85,
        ) == 1


def test_min_splits_returns_multi_beyond_tolerance(tmp_db):
    """Charge 1500 min > 2 × 408 → split nécessaire."""
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        _seed_ws(conn, "WS-G", 1.0)
        _seed_article_op(conn, "ART", "WS-G", 5.0)
        # 300 × 5 = 1500 min > 816 seuil → split
        # ceil(1500 / 408) = 4
        assert compute_min_splits(
            conn, "ART", 300, "WS-G", target_saturation=0.85,
        ) == 4


def test_min_splits_scales_with_low_capa(tmp_db):
    """Sur WS goulot capa 0.3, budget 122 min, seuil 244 min. Charge 500
    > seuil → split. ceil(500 / 122) = 5."""
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        _seed_ws(conn, "WS-B", 0.3)
        _seed_article_op(conn, "ART", "WS-B", 5.0)
        # 100 × 5 = 500 min > 244 seuil → split
        assert compute_min_splits(
            conn, "ART", 100, "WS-B", target_saturation=0.85,
        ) == 5


def test_split_candidates_no_change_when_fits(tmp_db):
    """Candidate tient dans budget → conservé tel quel, pas de nouveau."""
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        _seed_ws(conn, "WS-G", 1.0)
        _seed_article_op(conn, "ART", "WS-G", 2.0)
        conn.execute(
            "INSERT INTO candidate_orders "
            "(candidate_id, article_id, quantity, status) "
            "VALUES ('C1', 'ART', 100, 'candidate')"
        )
        candidates = [
            {"candidate_id": "C1", "article_id": "ART",
             "quantity": 100, "qty_in_contract": 100}
        ]
        out = split_candidates_for_takt(
            conn, candidates, "WS-G", target_saturation=0.85,
        )
        assert len(out) == 1
        assert out[0]["candidate_id"] == "C1"
        assert out[0]["qty_in_contract"] == 100


def test_split_candidates_creates_subs_only_beyond_tolerance(tmp_db):
    """Charge > 2 × budget → split. Qty totale préservée."""
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        _seed_ws(conn, "WS-G", 1.0)
        _seed_article_op(conn, "ART", "WS-G", 5.0)
        conn.execute(
            "INSERT INTO candidate_orders "
            "(candidate_id, article_id, quantity, status) "
            "VALUES ('C1', 'ART', 300, 'candidate')"
        )
        candidates = [
            {"candidate_id": "C1", "article_id": "ART",
             "quantity": 300, "qty_in_contract": 300}
        ]
        # 300 × 5 = 1500 min ; seuil = 2 × 408 = 816 → split.
        # ceil(1500/408) = 4 splits.
        out = split_candidates_for_takt(
            conn, candidates, "WS-G", target_saturation=0.85,
        )
        assert len(out) == 4
        assert out[0]["candidate_id"] == "C1"
        assert out[1]["candidate_id"] == "C1_split_1"
        # Qté totale préservée
        assert sum(c["qty_in_contract"] for c in out) == 300


def test_split_candidates_no_bottleneck_no_change(tmp_db):
    """Aucun goulot identifié → aucun split, minimise OFs."""
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        _seed_ws(conn, "WS-A", 1.0)
        _seed_article_op(conn, "ART", "WS-A", 20.0)  # charge énorme
        conn.execute(
            "INSERT INTO candidate_orders "
            "(candidate_id, article_id, quantity, status) "
            "VALUES ('C1', 'ART', 100, 'candidate')"
        )
        candidates = [
            {"candidate_id": "C1", "article_id": "ART",
             "quantity": 100, "qty_in_contract": 100}
        ]
        # bottleneck_ws = None → pas de split
        out = split_candidates_for_takt(
            conn, candidates, None, target_saturation=0.85,
        )
        assert len(out) == 1
        assert out[0]["candidate_id"] == "C1"
