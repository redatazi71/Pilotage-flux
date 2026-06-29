"""Tests des consommations matiere (V2)."""

from pathlib import Path

import pytest

from pilotage_flux.aps import (
    compute_candidates,
    persist_flattened_bom,
)
from pilotage_flux.db import db_session
from pilotage_flux.gates import run_p1_promotion
from pilotage_flux.importers import import_referentials
from pilotage_flux.mes import (
    compute_consumption_gaps,
    declare_consumption,
    list_consumptions,
)
from pilotage_flux.stocks_purchasing import get_stock, set_stock


@pytest.fixture
def db_with_of(tmp_db: Path, fixtures_v1_dir: Path) -> tuple[Path, str]:
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        compute_candidates(conn)
        persist_flattened_bom(conn)
        outcome = run_p1_promotion(conn)
        # On prend un OF ART-A
        of_id = next(
            o.of_id for o in outcome.ofs_created if o.article_id == "ART-A"
        )
    return tmp_db, of_id


def test_declare_consumption_creates_record(
    db_with_of: tuple[Path, str]
) -> None:
    db_path, of_id = db_with_of
    with db_session(db_path) as conn:
        c = declare_consumption(
            conn, of_id=of_id, article_id="COMP-X", qty_consumed=50,
            note="lot batch-1",
        )
        all_cons = list_consumptions(conn, of_id=of_id)
    assert c.of_id == of_id
    assert c.qty_consumed == 50
    assert c.note == "lot batch-1"
    assert len(all_cons) == 1


def test_declare_consumption_decrements_stock(
    db_with_of: tuple[Path, str]
) -> None:
    db_path, of_id = db_with_of
    with db_session(db_path) as conn:
        set_stock(conn, "COMP-X", 200)
        declare_consumption(
            conn, of_id=of_id, article_id="COMP-X", qty_consumed=80
        )
        stock_after = get_stock(conn, "COMP-X")
    assert stock_after.qty_available == 120


def test_declare_consumption_floor_at_zero(
    db_with_of: tuple[Path, str]
) -> None:
    """Si la consommation depasse le stock dispo, stock ne descend pas en negatif."""
    db_path, of_id = db_with_of
    with db_session(db_path) as conn:
        set_stock(conn, "COMP-X", 10)
        declare_consumption(
            conn, of_id=of_id, article_id="COMP-X", qty_consumed=50
        )
        stock_after = get_stock(conn, "COMP-X")
    assert stock_after.qty_available == 0


def test_declare_consumption_refuses_invalid_args(
    db_with_of: tuple[Path, str]
) -> None:
    db_path, of_id = db_with_of
    with db_session(db_path) as conn:
        with pytest.raises(ValueError, match="positif"):
            declare_consumption(
                conn, of_id=of_id, article_id="COMP-X", qty_consumed=0
            )
        with pytest.raises(ValueError, match="OF inconnu"):
            declare_consumption(
                conn, of_id="OF-X", article_id="COMP-X", qty_consumed=10
            )
        with pytest.raises(ValueError, match="Article inconnu"):
            declare_consumption(
                conn, of_id=of_id, article_id="X-INEXISTANT", qty_consumed=10
            )


def test_list_consumptions_filters_by_article(
    db_with_of: tuple[Path, str]
) -> None:
    db_path, of_id = db_with_of
    with db_session(db_path) as conn:
        declare_consumption(
            conn, of_id=of_id, article_id="COMP-X", qty_consumed=50
        )
        declare_consumption(
            conn, of_id=of_id, article_id="COMP-Y", qty_consumed=30
        )
        comp_x = list_consumptions(conn, of_id=of_id, article_id="COMP-X")
    assert len(comp_x) == 1
    assert comp_x[0].article_id == "COMP-X"


def test_compute_gaps_with_perfect_consumption(
    db_with_of: tuple[Path, str]
) -> None:
    """Si on consomme exactement la BOM, ecart = 0."""
    db_path, of_id = db_with_of
    with db_session(db_path) as conn:
        of = conn.execute(
            "SELECT quantity FROM manufacturing_orders WHERE of_id = ?",
            (of_id,),
        ).fetchone()
        of_qty = float(of["quantity"])
        # ART-A : besoin 2 COMP-X + 1 COMP-Y par unite (via SEMI-1)
        declare_consumption(
            conn, of_id=of_id, article_id="COMP-X", qty_consumed=2 * of_qty
        )
        declare_consumption(
            conn, of_id=of_id, article_id="COMP-Y", qty_consumed=1 * of_qty
        )
        gaps = compute_consumption_gaps(conn, of_id)
    gap_by_art = {g.article_id: g for g in gaps}
    assert gap_by_art["COMP-X"].gap == 0
    assert gap_by_art["COMP-Y"].gap == 0


def test_compute_gaps_detects_overconsumption(
    db_with_of: tuple[Path, str]
) -> None:
    db_path, of_id = db_with_of
    with db_session(db_path) as conn:
        of = conn.execute(
            "SELECT quantity FROM manufacturing_orders WHERE of_id = ?",
            (of_id,),
        ).fetchone()
        of_qty = float(of["quantity"])
        declare_consumption(
            conn,
            of_id=of_id, article_id="COMP-X",
            qty_consumed=2 * of_qty + 20,  # 20 unites de plus que la theorie
        )
        gaps = compute_consumption_gaps(conn, of_id)
    gap_by_art = {g.article_id: g for g in gaps}
    assert gap_by_art["COMP-X"].gap == 20
    assert gap_by_art["COMP-X"].gap_ratio > 0


def test_compute_gaps_detects_underconsumption(
    db_with_of: tuple[Path, str]
) -> None:
    db_path, of_id = db_with_of
    with db_session(db_path) as conn:
        of = conn.execute(
            "SELECT quantity FROM manufacturing_orders WHERE of_id = ?",
            (of_id,),
        ).fetchone()
        of_qty = float(of["quantity"])
        declare_consumption(
            conn,
            of_id=of_id, article_id="COMP-X",
            qty_consumed=2 * of_qty - 10,
        )
        gaps = compute_consumption_gaps(conn, of_id)
    gap_by_art = {g.article_id: g for g in gaps}
    assert gap_by_art["COMP-X"].gap == -10
