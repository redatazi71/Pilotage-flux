"""V13.J — Tests jumeau numérique 5 flux."""

from __future__ import annotations

from pilotage_flux.db import db_session
from pilotage_flux.flux.demand_contract import (
    create_demand_contract, sign_contract,
)
from pilotage_flux.flux.twin_state import (
    get_twin_history,
    get_twin_state,
    snapshot_twin_state,
)
from pilotage_flux.flux.weekly_contract import (
    compute_weekly_flux_contract,
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


def _seed_calendar(conn):
    conn.execute(
        "INSERT OR IGNORE INTO calendars "
        "(calendar_id, label, daily_minutes) VALUES ('CAL-DEFAULT', 't', 480)"
    )


def _setup_weekly(conn, so_id="SO-1", article="ART-A",
                    qty=100, due="2026-07-15"):
    """Seed minimal : 1 WS + 1 SO + 1 demand_contract + 1 weekly."""
    _seed_calendar(conn)
    _seed_ws(conn, "WS-3", 0.70)
    conn.execute(
        "INSERT OR IGNORE INTO articles (article_id, label) VALUES (?, ?)",
        (article, article),
    )
    conn.execute(
        "INSERT INTO sales_orders "
        "(sales_order_id, article_id, quantity, due_date) VALUES "
        "(?, ?, ?, ?)",
        (so_id, article, qty, due),
    )
    cid = create_demand_contract(
        conn, sales_order_id=so_id, article_id=article,
        quantity=qty, delivery_deadline=due,
        feasibility={
            "bottleneck_ws": "WS-3", "goulot_load_min": 500,
            "wip_predicted": 6.0,
        },
    )
    wid = compute_weekly_flux_contract(
        conn, year_iso=2026, week_iso=29,
    )
    return wid, cid


def test_snapshot_creates_empty_state(tmp_db):
    """Snapshot au j0 sans activité MES → tout à 0."""
    with db_session(tmp_db) as conn:
        wid, _ = _setup_weekly(conn)
        tid = snapshot_twin_state(
            conn, weekly_id=wid, snapshot_day=0,
            snapshot_date="2026-07-13",
        )
        assert tid > 0
        s = get_twin_state(conn, wid, 0)
        assert s is not None
        assert s.snapshot_day == 0
        assert s.physical_wip_actual == 0
        assert s.physical_ofs_running == 0
        assert s.physical_ofs_closed == 0
        assert s.info_deviations_detected == 0
        assert s.doc_contracts_draft == 1  # 1 contrat draft
        assert s.doc_contracts_signed == 0
        assert s.doc_contracts_closed == 0
        assert s.quality_yield_rate is None  # aucun OF


def test_snapshot_captures_daily_wip(tmp_db):
    with db_session(tmp_db) as conn:
        wid, _ = _setup_weekly(conn)
        snapshot_twin_state(
            conn, weekly_id=wid, snapshot_day=5,
            snapshot_date="2026-07-18", daily_wip=12.5,
        )
        s = get_twin_state(conn, wid, 5)
        assert s.physical_wip_actual == 12.5


def test_doc_status_reflects_contract_lifecycle(tmp_db):
    with db_session(tmp_db) as conn:
        wid, cid = _setup_weekly(conn)
        # Draft état initial
        snapshot_twin_state(
            conn, weekly_id=wid, snapshot_day=0,
            snapshot_date="2026-07-13",
        )
        assert get_twin_state(conn, wid, 0).doc_contracts_draft == 1
        # Après signature
        sign_contract(conn, cid)
        snapshot_twin_state(
            conn, weekly_id=wid, snapshot_day=3,
            snapshot_date="2026-07-16",
        )
        s = get_twin_state(conn, wid, 3)
        assert s.doc_contracts_draft == 0
        assert s.doc_contracts_signed == 1


def test_history_returns_ordered_snapshots(tmp_db):
    with db_session(tmp_db) as conn:
        wid, _ = _setup_weekly(conn)
        for d in [3, 0, 5, 1]:
            snapshot_twin_state(
                conn, weekly_id=wid, snapshot_day=d,
                snapshot_date=f"2026-07-{13+d:02d}",
                daily_wip=float(d),
            )
        history = get_twin_history(conn, wid)
        days = [h.snapshot_day for h in history]
        assert days == [0, 1, 3, 5]  # tri ascendant


def test_snapshot_updates_existing_entry(tmp_db):
    """Snapshot re-appelé sur (weekly, day) → REPLACE l'entrée."""
    with db_session(tmp_db) as conn:
        wid, _ = _setup_weekly(conn)
        snapshot_twin_state(
            conn, weekly_id=wid, snapshot_day=0,
            snapshot_date="2026-07-13", daily_wip=5.0,
        )
        snapshot_twin_state(
            conn, weekly_id=wid, snapshot_day=0,
            snapshot_date="2026-07-13", daily_wip=8.0,
        )
        history = get_twin_history(conn, wid)
        assert len(history) == 1  # pas de doublon
        assert history[0].physical_wip_actual == 8.0


def test_physical_ofs_counted_from_manufacturing_orders(tmp_db):
    """Snapshot compte les OFs liés au weekly via candidate."""
    with db_session(tmp_db) as conn:
        wid, cid = _setup_weekly(conn)
        # Ajoute un OF lié à un candidate lié à la SO du contrat
        # (la SO est dans le weekly)
        conn.execute(
            "INSERT INTO candidate_orders "
            "(candidate_id, sales_order_id, article_id, quantity) "
            "VALUES ('CAND-1', 'SO-1', 'ART-A', 100)"
        )
        conn.execute(
            "INSERT INTO manufacturing_orders "
            "(of_id, candidate_id, article_id, quantity, status, "
            " qty_good, qty_scrap) VALUES "
            "('OF-1', 'CAND-1', 'ART-A', 100, 'closed', 95, 5)"
        )
        snapshot_twin_state(
            conn, weekly_id=wid, snapshot_day=10,
            snapshot_date="2026-07-23",
        )
        s = get_twin_state(conn, wid, 10)
        assert s.physical_ofs_closed == 1
        assert s.physical_units_delivered == 95
        # Yield = 95 / 100
        assert s.quality_yield_rate == 0.95
        assert s.quality_scrap_cumul == 5.0
