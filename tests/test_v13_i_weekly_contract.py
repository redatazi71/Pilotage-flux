"""V13.I — Tests contrats de flux hebdomadaires."""

from __future__ import annotations

import pytest

from pilotage_flux.db import db_session
from pilotage_flux.flux.demand_contract import create_demand_contract
from pilotage_flux.flux.weekly_contract import (
    _iso_week_of,
    close_weekly_contract,
    compute_weekly_flux_contract,
    get_lines_of_weekly,
    get_weekly_flux_contract,
    sign_weekly_contract,
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


def _seed_so_with_contract(conn, so_id, article, qty, due_date,
                             bottleneck=None, charge_goulot=None,
                             wip_pred=None):
    conn.execute(
        "INSERT OR IGNORE INTO articles (article_id, label) VALUES (?, ?)",
        (article, article),
    )
    conn.execute(
        "INSERT INTO sales_orders "
        "(sales_order_id, article_id, quantity, due_date) "
        "VALUES (?, ?, ?, ?)",
        (so_id, article, qty, due_date),
    )
    feas = {}
    if bottleneck:
        feas["bottleneck_ws"] = bottleneck
    if charge_goulot is not None:
        feas["goulot_load_min"] = charge_goulot
    if wip_pred is not None:
        feas["wip_predicted"] = wip_pred
    return create_demand_contract(
        conn, sales_order_id=so_id, article_id=article,
        quantity=qty, delivery_deadline=due_date,
        feasibility=feas,
    )


def test_iso_week_computation():
    y, w, monday = _iso_week_of("2026-07-15")  # mercredi
    assert (y, w) == (2026, 29)
    assert monday == "2026-07-13"  # lundi


def test_compute_weekly_from_single_contract(tmp_db):
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        _seed_ws(conn, "WS-3", 0.70)
        _seed_so_with_contract(
            conn, "SO-1", "ART-A", 100, "2026-07-15",
            bottleneck="WS-3", charge_goulot=1500, wip_pred=8.0,
        )
        wid = compute_weekly_flux_contract(
            conn, year_iso=2026, week_iso=29,
        )
        w = get_weekly_flux_contract(conn, wid)
        assert w is not None
        assert w.year_iso == 2026
        assert w.week_iso == 29
        assert w.total_quantity == 100
        assert w.n_contracts == 1
        assert w.bottleneck_ws == "WS-3"
        assert w.status == "draft"
        # capa = 480 × 5 × 0.70 × 0.85 = 1428 min
        assert 1420 < w.capa_goulot_week < 1440
        # rho = charge / capa = 1500 / 1428 ≈ 1.05
        assert w.rho_bottleneck > 1.0
        # infeasible car rho > 1
        assert w.feasible is False


def test_compute_weekly_aggregates_multiple_contracts(tmp_db):
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        _seed_ws(conn, "WS-3", 0.70)
        _seed_so_with_contract(
            conn, "SO-1", "ART-A", 50, "2026-07-13",  # lundi
            bottleneck="WS-3", charge_goulot=400, wip_pred=5.0,
        )
        _seed_so_with_contract(
            conn, "SO-2", "ART-B", 30, "2026-07-15",  # mercredi
            bottleneck="WS-3", charge_goulot=300, wip_pred=3.0,
        )
        wid = compute_weekly_flux_contract(
            conn, year_iso=2026, week_iso=29,
        )
        w = get_weekly_flux_contract(conn, wid)
        assert w.n_contracts == 2
        assert w.total_quantity == 80
        assert w.bottleneck_ws == "WS-3"
        assert w.charge_goulot_week == 700  # 400 + 300
        assert w.wip_target == 8.0  # 5 + 3
        # takt = capa / total_qty = 1428 / 80 ≈ 17.8 min/unité
        assert w.takt_target_min is not None
        assert 17 < w.takt_target_min < 19


def test_bottleneck_chosen_by_max_charge(tmp_db):
    """Si plusieurs contrats ont des goulots différents, celui qui
    concentre la plus grande charge cumulée est retenu."""
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        _seed_ws(conn, "WS-3", 0.70)
        _seed_ws(conn, "WS-6", 0.60)
        _seed_so_with_contract(
            conn, "SO-1", "ART-A", 50, "2026-07-13",
            bottleneck="WS-3", charge_goulot=200,
        )
        _seed_so_with_contract(
            conn, "SO-2", "ART-B", 100, "2026-07-15",
            bottleneck="WS-6", charge_goulot=500,
        )
        wid = compute_weekly_flux_contract(
            conn, year_iso=2026, week_iso=29,
        )
        w = get_weekly_flux_contract(conn, wid)
        # WS-6 concentre 500 min vs WS-3 200 min → goulot dominant
        assert w.bottleneck_ws == "WS-6"


def test_raise_if_no_contracts_in_week(tmp_db):
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        with pytest.raises(ValueError, match="Aucun demand_contract"):
            compute_weekly_flux_contract(
                conn, year_iso=2026, week_iso=29,
            )


def test_weekly_lines_linked_to_contracts(tmp_db):
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        _seed_ws(conn, "WS-3", 0.7)
        c1 = _seed_so_with_contract(
            conn, "SO-1", "ART-A", 50, "2026-07-14",
            bottleneck="WS-3",
        )
        c2 = _seed_so_with_contract(
            conn, "SO-2", "ART-B", 50, "2026-07-16",
            bottleneck="WS-3",
        )
        wid = compute_weekly_flux_contract(
            conn, year_iso=2026, week_iso=29,
        )
        lines = get_lines_of_weekly(conn, wid)
        assert set(lines) == {c1, c2}


def test_sign_and_close_weekly(tmp_db):
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        _seed_ws(conn, "WS-3", 0.7)
        _seed_so_with_contract(
            conn, "SO-1", "ART-A", 50, "2026-07-14",
            bottleneck="WS-3",
        )
        wid = compute_weekly_flux_contract(
            conn, year_iso=2026, week_iso=29,
        )
        assert get_weekly_flux_contract(conn, wid).status == "draft"
        sign_weekly_contract(conn, wid)
        assert get_weekly_flux_contract(conn, wid).status == "signed"
        close_weekly_contract(conn, wid)
        assert get_weekly_flux_contract(conn, wid).status == "closed"


def test_feasible_when_rho_below_1(tmp_db):
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        _seed_ws(conn, "WS-3", 0.90)  # capa haute
        _seed_so_with_contract(
            conn, "SO-1", "ART-A", 100, "2026-07-14",
            bottleneck="WS-3", charge_goulot=500,
        )
        wid = compute_weekly_flux_contract(
            conn, year_iso=2026, week_iso=29,
        )
        w = get_weekly_flux_contract(conn, wid)
        # capa = 480 × 5 × 0.90 × 0.85 = 1836 min
        # rho = 500 / 1836 ≈ 0.27
        assert w.rho_bottleneck < 0.5
        assert w.feasible is True
