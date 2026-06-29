"""Tests des stocks et achats ouverts (V2)."""

from pathlib import Path

import pytest

from pilotage_flux.db import db_session
from pilotage_flux.importers import import_referentials
from pilotage_flux.stocks_purchasing import (
    cancel_purchase,
    create_purchase,
    get_stock,
    list_purchases,
    list_stocks,
    open_qty,
    project_available,
    receive_purchase,
    reserve,
    set_stock,
    unreserve,
)


@pytest.fixture
def db_with_refs(tmp_db: Path, fixtures_v1_dir: Path) -> Path:
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
    return tmp_db


# -----------------------------------------------------------------------
# Stocks
# -----------------------------------------------------------------------

def test_get_stock_returns_zero_for_unknown(db_with_refs: Path) -> None:
    with db_session(db_with_refs) as conn:
        s = get_stock(conn, "COMP-X")
    assert s.qty_available == 0.0
    assert s.qty_reserved == 0.0


def test_set_stock_is_idempotent(db_with_refs: Path) -> None:
    with db_session(db_with_refs) as conn:
        set_stock(conn, "COMP-X", 500)
        s1 = get_stock(conn, "COMP-X")
        set_stock(conn, "COMP-X", 700)
        s2 = get_stock(conn, "COMP-X")
    assert s1.qty_available == 500
    assert s2.qty_available == 700


def test_set_stock_refuses_unknown_article(db_with_refs: Path) -> None:
    with db_session(db_with_refs) as conn:
        with pytest.raises(ValueError, match="inconnu"):
            set_stock(conn, "ART-INEXISTANT", 100)


def test_reserve_increases_reserved(db_with_refs: Path) -> None:
    with db_session(db_with_refs) as conn:
        set_stock(conn, "COMP-X", 500)
        reserve(conn, "COMP-X", 100)
        reserve(conn, "COMP-X", 50)
        s = get_stock(conn, "COMP-X")
    assert s.qty_available == 500
    assert s.qty_reserved == 150
    assert s.qty_free == 350


def test_unreserve_decreases_reserved_not_below_zero(db_with_refs: Path) -> None:
    with db_session(db_with_refs) as conn:
        set_stock(conn, "COMP-X", 500)
        reserve(conn, "COMP-X", 100)
        unreserve(conn, "COMP-X", 200)  # plus que reservé
        s = get_stock(conn, "COMP-X")
    assert s.qty_reserved == 0


def test_list_stocks_returns_all(db_with_refs: Path) -> None:
    with db_session(db_with_refs) as conn:
        set_stock(conn, "COMP-X", 500)
        set_stock(conn, "COMP-Y", 300)
        stocks = list_stocks(conn)
    assert {s.article_id for s in stocks} == {"COMP-X", "COMP-Y"}


# -----------------------------------------------------------------------
# Purchase orders
# -----------------------------------------------------------------------

def test_create_purchase_returns_po_with_id(db_with_refs: Path) -> None:
    with db_session(db_with_refs) as conn:
        po = create_purchase(
            conn, article_id="COMP-X", qty_ordered=200, expected_at="2026-07-15"
        )
    assert po.po_id == "PO-0001"
    assert po.qty_ordered == 200
    assert po.qty_received == 0
    assert po.status == "open"


def test_create_purchase_refuses_negative_qty(db_with_refs: Path) -> None:
    with db_session(db_with_refs) as conn:
        with pytest.raises(ValueError, match="strictement positif"):
            create_purchase(conn, article_id="COMP-X", qty_ordered=0)


def test_receive_full_qty_changes_status_to_received(db_with_refs: Path) -> None:
    with db_session(db_with_refs) as conn:
        po = create_purchase(conn, article_id="COMP-X", qty_ordered=100)
        result = receive_purchase(conn, po.po_id, qty_received=100)
        stock = get_stock(conn, "COMP-X")
    assert result.status == "received"
    assert result.qty_received == 100
    assert result.received_at is not None
    assert stock.qty_available == 100


def test_partial_receive_keeps_status_partial(db_with_refs: Path) -> None:
    with db_session(db_with_refs) as conn:
        po = create_purchase(conn, article_id="COMP-X", qty_ordered=100)
        receive_purchase(conn, po.po_id, qty_received=30)
        result = receive_purchase(conn, po.po_id, qty_received=20)
        stock = get_stock(conn, "COMP-X")
    assert result.qty_received == 50
    assert result.status == "partial"
    assert stock.qty_available == 50


def test_receive_above_ordered_raises(db_with_refs: Path) -> None:
    with db_session(db_with_refs) as conn:
        po = create_purchase(conn, article_id="COMP-X", qty_ordered=100)
        with pytest.raises(ValueError, match="depasse"):
            receive_purchase(conn, po.po_id, qty_received=150)


def test_cannot_receive_on_received_or_cancelled(db_with_refs: Path) -> None:
    with db_session(db_with_refs) as conn:
        po = create_purchase(conn, article_id="COMP-X", qty_ordered=100)
        receive_purchase(conn, po.po_id, qty_received=100)
        with pytest.raises(ValueError, match="reception impossible"):
            receive_purchase(conn, po.po_id, qty_received=10)
        po2 = create_purchase(conn, article_id="COMP-X", qty_ordered=50)
        cancel_purchase(conn, po2.po_id)
        with pytest.raises(ValueError, match="reception impossible"):
            receive_purchase(conn, po2.po_id, qty_received=10)


def test_open_qty_sums_open_and_partial(db_with_refs: Path) -> None:
    with db_session(db_with_refs) as conn:
        po1 = create_purchase(conn, article_id="COMP-X", qty_ordered=100)
        po2 = create_purchase(conn, article_id="COMP-X", qty_ordered=80)
        receive_purchase(conn, po1.po_id, qty_received=30)  # partial
        po3 = create_purchase(conn, article_id="COMP-X", qty_ordered=50)
        receive_purchase(conn, po3.po_id, qty_received=50)  # received -> exclu
        cancel_purchase(conn, po2.po_id)  # cancelled -> exclu
        q = open_qty(conn, "COMP-X")
    # Reste : po1 (100-30=70), po2 cancelled, po3 received
    assert q == 70


# -----------------------------------------------------------------------
# Projection (R-P2-05 V2 enrichi)
# -----------------------------------------------------------------------

def test_project_available_combines_stock_and_open_po(db_with_refs: Path) -> None:
    with db_session(db_with_refs) as conn:
        set_stock(conn, "COMP-X", 100)
        reserve(conn, "COMP-X", 30)
        create_purchase(conn, article_id="COMP-X", qty_ordered=200)
        projected = project_available(conn, "COMP-X")
    # free = 100 - 30 = 70 + open PO 200 = 270
    assert projected == 270


def test_project_available_zero_when_nothing(db_with_refs: Path) -> None:
    with db_session(db_with_refs) as conn:
        projected = project_available(conn, "COMP-X")
    assert projected == 0.0
