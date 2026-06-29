"""Tests des vues de flux V0."""

from pathlib import Path

import pytest

from pilotage_flux.db import db_session
from pilotage_flux.gates import run_p1_promotion
from pilotage_flux.importers import import_referentials
from pilotage_flux.mes import close_of, finish_operation, launch_of, start_operation
from pilotage_flux.visualization import of_detail_view, workstation_view


@pytest.fixture
def db_executed(tmp_db: Path, fixtures_dir: Path) -> tuple[Path, str]:
    """Base avec un OF execute (lancement + 3 ops faites + cloture)."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_dir)
        outcome = run_p1_promotion(conn)
        of_id = outcome.ofs_created[0].of_id
        launch_of(conn, of_id)
        ops = conn.execute(
            "SELECT of_op_id FROM order_operations WHERE of_id = ? ORDER BY sequence_idx",
            (of_id,),
        ).fetchall()
        for op in ops:
            start_operation(conn, op["of_op_id"])
            finish_operation(conn, op["of_op_id"], qty_good=95.0, qty_scrap=5.0)
        close_of(conn, of_id)
    return tmp_db, of_id


def test_workstation_view_returns_all_stations(db_executed: tuple[Path, str]) -> None:
    db_path, _ = db_executed
    with db_session(db_path) as conn:
        views = workstation_view(conn)
    assert len(views) == 3
    assert [v.workstation_id for v in views] == ["WS-1", "WS-2", "WS-3"]


def test_workstation_view_counts_done_ops(db_executed: tuple[Path, str]) -> None:
    """Le 1er OF est entierement fini ; le 2e n'a pas demarre."""
    db_path, _ = db_executed
    with db_session(db_path) as conn:
        views = workstation_view(conn)
    by_ws = {v.workstation_id: v for v in views}
    # WS-1 : 1 done (OF-0001) + 1 pending (OF-0002)
    assert len(by_ws["WS-1"].done) == 1
    assert len(by_ws["WS-1"].pending) == 1
    assert by_ws["WS-1"].wip == 0


def test_workstation_view_detects_wip(tmp_db: Path, fixtures_dir: Path) -> None:
    """Une operation en cours est comptee dans wip."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_dir)
        outcome = run_p1_promotion(conn)
        of_id = outcome.ofs_created[0].of_id
        launch_of(conn, of_id)
        first_op = conn.execute(
            "SELECT of_op_id, workstation_id FROM order_operations WHERE of_id = ? ORDER BY sequence_idx LIMIT 1",
            (of_id,),
        ).fetchone()
        start_operation(conn, first_op["of_op_id"])

        views = workstation_view(conn)
        by_ws = {v.workstation_id: v for v in views}
    assert by_ws[first_op["workstation_id"]].wip == 1


def test_of_detail_view_returns_complete_state(db_executed: tuple[Path, str]) -> None:
    db_path, of_id = db_executed
    with db_session(db_path) as conn:
        detail = of_detail_view(conn, of_id)
    assert detail is not None
    assert detail.status == "closed"
    assert detail.qty_good == 95.0
    assert len(detail.operations) == 3
    # OF_CREATED + OF_LAUNCHED + 3*(START+FINISH) + OF_CLOSED = 9
    assert len(detail.events) == 9
    # Chaque operation a 2 declarations (start + finish)
    for op in detail.operations:
        assert len(op.declarations) == 2


def test_of_detail_view_returns_none_for_unknown(db_executed: tuple[Path, str]) -> None:
    db_path, _ = db_executed
    with db_session(db_path) as conn:
        detail = of_detail_view(conn, "OF-INEXISTANT")
    assert detail is None
