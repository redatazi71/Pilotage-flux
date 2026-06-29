"""Tests du moteur MES V0 : lancement, declarations, cloture."""

from pathlib import Path

import pytest

from pilotage_flux.db import db_session
from pilotage_flux.events import reconstruct_of
from pilotage_flux.gates import run_p1_promotion
from pilotage_flux.importers import import_referentials
from pilotage_flux.mes import (
    close_of,
    finish_operation,
    launch_of,
    start_operation,
)


@pytest.fixture
def db_with_ofs(tmp_db: Path, fixtures_dir: Path) -> Path:
    """Base initialisee + referentiels + P1 execute (2 OF crees)."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_dir)
        run_p1_promotion(conn)
    return tmp_db


def _first_of_id(db_path: Path) -> str:
    with db_session(db_path) as conn:
        return conn.execute(
            "SELECT of_id FROM manufacturing_orders ORDER BY of_id LIMIT 1"
        ).fetchone()["of_id"]


def test_launch_changes_status_and_emits_event(db_with_ofs: Path) -> None:
    of_id = _first_of_id(db_with_ofs)
    with db_session(db_with_ofs) as conn:
        result = launch_of(conn, of_id)
        of_row = conn.execute(
            "SELECT status FROM manufacturing_orders WHERE of_id = ?", (of_id,)
        ).fetchone()
        ev = conn.execute(
            "SELECT * FROM event_store WHERE event_id = ?", (result.event_id,)
        ).fetchone()
    assert of_row["status"] == "launched"
    assert ev["event_type"] == "OF_LAUNCHED"


def test_launch_refuses_non_created_of(db_with_ofs: Path) -> None:
    of_id = _first_of_id(db_with_ofs)
    with db_session(db_with_ofs) as conn:
        launch_of(conn, of_id)
        with pytest.raises(ValueError, match="created"):
            launch_of(conn, of_id)


def test_start_finish_operation_full_cycle(db_with_ofs: Path) -> None:
    of_id = _first_of_id(db_with_ofs)
    with db_session(db_with_ofs) as conn:
        launch_of(conn, of_id)
        ops = conn.execute(
            "SELECT of_op_id FROM order_operations WHERE of_id = ? ORDER BY sequence_idx",
            (of_id,),
        ).fetchall()
        op_ids = [o["of_op_id"] for o in ops]

        # Op 1
        d_start = start_operation(conn, op_ids[0])
        d_finish = finish_operation(
            conn, op_ids[0], qty_good=95.0, qty_scrap=5.0
        )

        op_row = conn.execute(
            "SELECT * FROM order_operations WHERE of_op_id = ?", (op_ids[0],)
        ).fetchone()
        of_row = conn.execute(
            "SELECT status FROM manufacturing_orders WHERE of_id = ?", (of_id,)
        ).fetchone()
        decls = conn.execute(
            "SELECT kind FROM mes_declarations WHERE of_op_id = ? ORDER BY declaration_id",
            (op_ids[0],),
        ).fetchall()

    assert op_row["status"] == "done"
    assert op_row["qty_good"] == 95.0
    assert op_row["qty_scrap"] == 5.0
    assert of_row["status"] == "in_progress"
    assert [d["kind"] for d in decls] == ["start", "finish"]
    assert d_start.kind == "start"
    assert d_finish.kind == "finish"


def test_finish_refuses_negative_quantities(db_with_ofs: Path) -> None:
    of_id = _first_of_id(db_with_ofs)
    with db_session(db_with_ofs) as conn:
        launch_of(conn, of_id)
        op_id = conn.execute(
            "SELECT of_op_id FROM order_operations WHERE of_id = ? ORDER BY sequence_idx LIMIT 1",
            (of_id,),
        ).fetchone()["of_op_id"]
        start_operation(conn, op_id)
        with pytest.raises(ValueError, match="positives"):
            finish_operation(conn, op_id, qty_good=-1.0)


def test_close_refuses_with_pending_operations(db_with_ofs: Path) -> None:
    of_id = _first_of_id(db_with_ofs)
    with db_session(db_with_ofs) as conn:
        launch_of(conn, of_id)
        with pytest.raises(ValueError, match="non terminee"):
            close_of(conn, of_id)


def test_full_golden_path(db_with_ofs: Path) -> None:
    """Cycle complet : launch -> 3 ops -> close, avec event sourcing complet."""
    of_id = _first_of_id(db_with_ofs)
    with db_session(db_with_ofs) as conn:
        launch_of(conn, of_id)
        ops = conn.execute(
            "SELECT of_op_id FROM order_operations WHERE of_id = ? ORDER BY sequence_idx",
            (of_id,),
        ).fetchall()
        for op in ops:
            start_operation(conn, op["of_op_id"])
            finish_operation(conn, op["of_op_id"], qty_good=95.0, qty_scrap=5.0)
        close_result = close_of(conn, of_id)

        of_row = conn.execute(
            "SELECT * FROM manufacturing_orders WHERE of_id = ?", (of_id,)
        ).fetchone()
        events = conn.execute(
            "SELECT event_type FROM event_store WHERE aggregate_id = ? ORDER BY event_id",
            (of_id,),
        ).fetchall()
        p4 = conn.execute(
            "SELECT * FROM gate_decisions WHERE subject_id = ? AND gate = 'P4'",
            (of_id,),
        ).fetchone()

    assert of_row["status"] == "closed"
    assert close_result.qty_good == 95.0
    assert close_result.qty_scrap == 5.0
    # Sequence d'events : OF_CREATED + OF_LAUNCHED + 3 x (OP_STARTED + OP_FINISHED) + OF_CLOSED
    types = [e["event_type"] for e in events]
    assert types == [
        "OF_CREATED",
        "OF_LAUNCHED",
        "OP_STARTED",
        "OP_FINISHED",
        "OP_STARTED",
        "OP_FINISHED",
        "OP_STARTED",
        "OP_FINISHED",
        "OF_CLOSED",
    ]
    assert p4 is not None
    assert p4["decision"] == "CLOSE"


def test_reconstruction_from_event_store_alone(db_with_ofs: Path) -> None:
    """L'etat final est reconstruisible depuis les seuls evenements."""
    of_id = _first_of_id(db_with_ofs)
    with db_session(db_with_ofs) as conn:
        launch_of(conn, of_id)
        ops = conn.execute(
            "SELECT of_op_id FROM order_operations WHERE of_id = ? ORDER BY sequence_idx",
            (of_id,),
        ).fetchall()
        for op in ops:
            start_operation(conn, op["of_op_id"])
            finish_operation(conn, op["of_op_id"], qty_good=95.0, qty_scrap=5.0)
        close_of(conn, of_id)

        state = reconstruct_of(conn, of_id)

    assert state.status == "closed"
    assert state.article_id == "ART-A"
    assert state.quantity == 100.0
    assert state.qty_good == 95.0
    assert state.qty_scrap == 5.0
    assert len(state.operations_started) == 3
    assert len(state.operations_finished) == 3
    # OF_CREATED + OF_LAUNCHED + 3*(START+FINISH) + OF_CLOSED = 9
    assert state.event_count == 9
