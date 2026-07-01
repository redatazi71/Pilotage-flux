"""V13.H — Tests contrats de production (zone négociable enrichie)."""

from __future__ import annotations

from pilotage_flux.db import db_session
from pilotage_flux.flux.demand_contract import (
    check_appro_status,
    close_contract,
    create_demand_contract,
    get_infeasible_contracts,
    get_demand_contract,
    get_demand_contracts_by_so,
    sign_contract,
)


def _seed_so(conn, so_id, article, qty, due):
    conn.execute(
        "INSERT OR IGNORE INTO articles (article_id, label) VALUES (?, ?)",
        (article, article),
    )
    conn.execute(
        "INSERT INTO sales_orders "
        "(sales_order_id, article_id, quantity, due_date) "
        "VALUES (?, ?, ?, ?)",
        (so_id, article, qty, due),
    )


def test_create_minimal_contract(tmp_db):
    with db_session(tmp_db) as conn:
        _seed_so(conn, "SO-1", "ART-A", 100, "2026-07-30")
        cid = create_demand_contract(
            conn, sales_order_id="SO-1", article_id="ART-A",
            quantity=100, delivery_deadline="2026-07-30",
        )
        assert cid.startswith("PC-")
        contract = get_demand_contract(conn, cid)
        assert contract is not None
        assert contract.sales_order_id == "SO-1"
        assert contract.article_id == "ART-A"
        assert contract.quantity == 100
        assert contract.feasible is True  # default with empty feasibility
        assert contract.flux_physical_status == "planned"
        assert contract.flux_doc_status == "draft"


def test_create_contract_with_feasibility(tmp_db):
    """Vérifie que le dossier de faisabilité V13.E est bien persisté."""
    with db_session(tmp_db) as conn:
        _seed_so(conn, "SO-1", "ART-A", 100, "2026-07-30")
        feasibility = {
            "bottleneck_ws": "WS-3",
            "goulot_slot_day": 5,
            "launch_day": 3,
            "buffer_days": 2,
            "charge_total_min": 1200.0,
            "goulot_load_min": 400.0,
            "takt_min_per_unit_target": 12.0,
            "wip_predicted": 8.5,
            "rho_bottleneck_run": 0.83,
            "feasible": 1,
        }
        cid = create_demand_contract(
            conn, sales_order_id="SO-1", article_id="ART-A",
            quantity=100, delivery_deadline="2026-07-30",
            feasibility=feasibility,
        )
        contract = get_demand_contract(conn, cid)
        assert contract.bottleneck_ws == "WS-3"
        assert contract.takt_target_min == 12.0
        assert contract.wip_target == 8.5
        assert contract.wip_predicted == 8.5
        assert contract.charge_total_min == 1200.0
        assert contract.charge_bottleneck_min == 400.0
        assert contract.rho_bottleneck == 0.83
        assert contract.buffer_days == 2
        assert contract.feasible is True


def test_infeasible_flag_persisted(tmp_db):
    with db_session(tmp_db) as conn:
        _seed_so(conn, "SO-2", "ART-B", 100, "2026-07-30")
        cid = create_demand_contract(
            conn, sales_order_id="SO-2", article_id="ART-B",
            quantity=100, delivery_deadline="2026-07-30",
            feasibility={"feasible": 0, "rho_bottleneck_run": 1.2},
        )
        contract = get_demand_contract(conn, cid)
        assert contract.feasible is False
        assert contract.rho_bottleneck == 1.2

        infeasibles = get_infeasible_contracts(conn)
        assert len(infeasibles) == 1
        assert infeasibles[0].contract_id == cid


def test_multiple_contracts_per_so(tmp_db):
    """Une SO peut donner plusieurs contrats (split V13.F)."""
    with db_session(tmp_db) as conn:
        _seed_so(conn, "SO-1", "ART-A", 200, "2026-07-30")
        c1 = create_demand_contract(
            conn, sales_order_id="SO-1", article_id="ART-A",
            quantity=100, delivery_deadline="2026-07-30",
        )
        c2 = create_demand_contract(
            conn, sales_order_id="SO-1", article_id="ART-A",
            quantity=100, delivery_deadline="2026-07-30",
        )
        contracts = get_demand_contracts_by_so(conn, "SO-1")
        assert len(contracts) == 2
        assert {c.contract_id for c in contracts} == {c1, c2}


def test_sign_contract_updates_status(tmp_db):
    with db_session(tmp_db) as conn:
        _seed_so(conn, "SO-1", "ART-A", 100, "2026-07-30")
        cid = create_demand_contract(
            conn, sales_order_id="SO-1", article_id="ART-A",
            quantity=100, delivery_deadline="2026-07-30",
        )
        assert get_demand_contract(conn, cid).flux_doc_status == "draft"
        sign_contract(conn, cid)
        contract = get_demand_contract(conn, cid)
        assert contract.flux_doc_status == "signed"


def test_close_contract_updates_status(tmp_db):
    with db_session(tmp_db) as conn:
        _seed_so(conn, "SO-1", "ART-A", 100, "2026-07-30")
        cid = create_demand_contract(
            conn, sales_order_id="SO-1", article_id="ART-A",
            quantity=100, delivery_deadline="2026-07-30",
        )
        close_contract(conn, cid)
        contract = get_demand_contract(conn, cid)
        assert contract.flux_physical_status == "closed"


def test_appro_ok_when_stock_sufficient(tmp_db):
    with db_session(tmp_db) as conn:
        _seed_so(conn, "SO-1", "ART-A", 100, "2026-07-30")
        conn.execute(
            "INSERT OR IGNORE INTO articles (article_id, label) "
            "VALUES ('COMP-X', 'X')"
        )
        conn.execute(
            "INSERT INTO bom_lines (parent_article, child_article, quantity) "
            "VALUES ('ART-A', 'COMP-X', 2)"
        )
        conn.execute(
            "INSERT INTO stocks (article_id, qty_available) VALUES ('COMP-X', 500)"
        )
        cid = create_demand_contract(
            conn, sales_order_id="SO-1", article_id="ART-A",
            quantity=100, delivery_deadline="2026-07-30",
        )
        # 100 × 2 = 200 needed, 500 in stock → ok
        assert check_appro_status(conn, cid) == "ok"


def test_appro_partial_when_stock_below_need(tmp_db):
    with db_session(tmp_db) as conn:
        _seed_so(conn, "SO-1", "ART-A", 100, "2026-07-30")
        conn.execute(
            "INSERT OR IGNORE INTO articles (article_id, label) "
            "VALUES ('COMP-X', 'X')"
        )
        conn.execute(
            "INSERT INTO bom_lines (parent_article, child_article, quantity) "
            "VALUES ('ART-A', 'COMP-X', 2)"
        )
        conn.execute(
            "INSERT INTO stocks (article_id, qty_available) VALUES ('COMP-X', 150)"
        )
        cid = create_demand_contract(
            conn, sales_order_id="SO-1", article_id="ART-A",
            quantity=100, delivery_deadline="2026-07-30",
        )
        # 200 needed, 150 stock → partial (available >= 50% needed)
        assert check_appro_status(conn, cid) == "partial"


def test_appro_missing_when_stock_below_half(tmp_db):
    with db_session(tmp_db) as conn:
        _seed_so(conn, "SO-1", "ART-A", 100, "2026-07-30")
        conn.execute(
            "INSERT OR IGNORE INTO articles (article_id, label) "
            "VALUES ('COMP-X', 'X')"
        )
        conn.execute(
            "INSERT INTO bom_lines (parent_article, child_article, quantity) "
            "VALUES ('ART-A', 'COMP-X', 2)"
        )
        conn.execute(
            "INSERT INTO stocks (article_id, qty_available) VALUES ('COMP-X', 50)"
        )
        cid = create_demand_contract(
            conn, sales_order_id="SO-1", article_id="ART-A",
            quantity=100, delivery_deadline="2026-07-30",
        )
        # 200 needed, 50 stock (< 100 = 50%) → missing
        assert check_appro_status(conn, cid) == "missing"


def test_appro_no_bom_returns_ok(tmp_db):
    """Article sans BOM (acheté) → toujours ok pour l'appro."""
    with db_session(tmp_db) as conn:
        _seed_so(conn, "SO-1", "ART-Z", 100, "2026-07-30")
        cid = create_demand_contract(
            conn, sales_order_id="SO-1", article_id="ART-Z",
            quantity=100, delivery_deadline="2026-07-30",
        )
        assert check_appro_status(conn, cid) == "ok"
