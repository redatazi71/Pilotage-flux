"""Tests du moteur APS V0 : CBN, capacite, planner."""

from pathlib import Path

import pytest

from pilotage_flux.aps import (
    compute_candidates,
    compute_load_by_workstation,
    promote_candidate_to_of,
)
from pilotage_flux.db import db_session
from pilotage_flux.importers import import_referentials


@pytest.fixture
def db_with_refs(tmp_db: Path, fixtures_dir: Path) -> Path:
    """Base initialisee avec referentiels importes."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_dir)
    return tmp_db


def test_cbn_creates_one_candidate_per_sales_order(db_with_refs: Path) -> None:
    with db_session(db_with_refs) as conn:
        created = compute_candidates(conn)
    assert len(created) == 2
    # Tries par due_date ASC
    assert created[0].sales_order_id == "SO-001"
    assert created[0].quantity == 100
    assert created[0].article_id == "ART-A"
    assert created[1].sales_order_id == "SO-002"
    assert created[1].quantity == 50


def test_cbn_is_idempotent(db_with_refs: Path) -> None:
    with db_session(db_with_refs) as conn:
        first = compute_candidates(conn)
        second = compute_candidates(conn)
    assert len(first) == 2
    assert len(second) == 0


def test_capacity_load_includes_all_workstations(db_with_refs: Path) -> None:
    with db_session(db_with_refs) as conn:
        compute_candidates(conn)
        loads = compute_load_by_workstation(conn)
    by_ws = {w.workstation_id: w for w in loads}
    # 100 ART-A : 100 x 2.5 = 250 min sur WS-1 ; 50 ART-A : 50 x 2.5 = 125 ; total 375
    assert by_ws["WS-1"].load_minutes == pytest.approx(375.0)
    # WS-2 : (100 + 50) x 3.0 = 450 min
    assert by_ws["WS-2"].load_minutes == pytest.approx(450.0)
    # WS-3 : (100 + 50) x 1.2 = 180 min
    assert by_ws["WS-3"].load_minutes == pytest.approx(180.0)
    # capacite WS-1 = 480 x 0.85 = 408 ; charge 375 -> pas surcharge
    assert by_ws["WS-1"].daily_capacity_minutes == pytest.approx(408.0)
    assert by_ws["WS-1"].is_overloaded is False
    # capacite WS-2 = 480 x 0.80 = 384 ; charge 450 -> surcharge
    assert by_ws["WS-2"].is_overloaded is True


def test_capacity_is_data_driven(db_with_refs: Path) -> None:
    """Modifier le capacity_factor en base change le verdict de surcharge."""
    with db_session(db_with_refs) as conn:
        compute_candidates(conn)
        before = {
            w.workstation_id: w.is_overloaded
            for w in compute_load_by_workstation(conn)
        }
        assert before["WS-2"] is True

        # On augmente la capacite de WS-2 sans toucher au code
        conn.execute(
            "UPDATE parameters SET value_num = 1.10 "
            "WHERE scope='workstation' AND scope_ref='WS-2' AND name='capacity_factor'"
        )
        after = {
            w.workstation_id: w.is_overloaded
            for w in compute_load_by_workstation(conn)
        }
    assert after["WS-2"] is False


def test_promote_creates_of_with_operations(db_with_refs: Path) -> None:
    with db_session(db_with_refs) as conn:
        candidates = compute_candidates(conn)
        cid = candidates[0].candidate_id
        result = promote_candidate_to_of(conn, cid)

        of_row = conn.execute(
            "SELECT * FROM manufacturing_orders WHERE of_id = ?", (result.of_id,)
        ).fetchone()
        ops = conn.execute(
            "SELECT * FROM order_operations WHERE of_id = ? ORDER BY sequence_idx",
            (result.of_id,),
        ).fetchall()
        events = conn.execute(
            "SELECT * FROM event_store WHERE aggregate_id = ?", (result.of_id,)
        ).fetchall()
        decisions = conn.execute(
            "SELECT * FROM gate_decisions WHERE subject_id = ?", (result.of_id,)
        ).fetchall()
        cand_after = conn.execute(
            "SELECT status FROM candidate_orders WHERE candidate_id = ?", (cid,)
        ).fetchone()

    assert of_row["article_id"] == "ART-A"
    assert of_row["quantity"] == 100
    assert of_row["status"] == "created"
    assert len(ops) == 3
    assert [o["workstation_id"] for o in ops] == ["WS-1", "WS-2", "WS-3"]
    assert len(events) == 1
    assert events[0]["event_type"] == "OF_CREATED"
    assert len(decisions) == 1
    assert decisions[0]["gate"] == "P1"
    assert decisions[0]["decision"] == "CREATE"
    assert cand_after["status"] == "promoted"


def test_promote_refuses_already_promoted(db_with_refs: Path) -> None:
    with db_session(db_with_refs) as conn:
        candidates = compute_candidates(conn)
        cid = candidates[0].candidate_id
        promote_candidate_to_of(conn, cid)
        with pytest.raises(ValueError, match="statut"):
            promote_candidate_to_of(conn, cid)
