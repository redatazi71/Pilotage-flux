"""Tests de la porte P3 inverse (RETOUR_NEGOCIABLE + FRAGMENT)."""

from pathlib import Path

import pytest

from pilotage_flux.aps import compute_candidates
from pilotage_flux.db import db_session
from pilotage_flux.flux import (
    compute_coherence,
    create_contract,
)
from pilotage_flux.gates import (
    DECISION_FREEZE,
    fragment_of,
    get_lineage,
    return_to_negociable,
    run_p1_promotion,
    run_p2_on_libre_zone,
    run_p3_freeze,
)
from pilotage_flux.importers import import_referentials
from pilotage_flux.mes import close_of, finish_operation, launch_of, start_operation
from pilotage_flux.risk_debt import extinguish_risk_debt, list_risk_debts
from pilotage_flux.zones import ZONE_GELEE, ZONE_NEGOCIABLE, current_zone


def _frozen_state(db_path: Path, fixtures_v1_dir: Path) -> tuple[str, list[str]]:
    """Helper : prepare base avec contrat freeze + OF crees par P1 legacy.

    Renvoie (contract_id, candidate_ids).
    """
    with db_session(db_path) as conn:
        import_referentials(conn, fixtures_v1_dir)
        compute_candidates(conn)
        # P1 legacy : cree les OF directement (mais ne touche pas la zone)
        run_p1_promotion(conn)
        # P2 deplace candidates libre -> negociable
        run_p2_on_libre_zone(conn)
        cids = [
            r["candidate_id"]
            for r in conn.execute(
                "SELECT candidate_id FROM candidate_orders ORDER BY candidate_id"
            )
        ]
        contract = create_contract(
            conn,
            horizon_label="W27",
            horizon_start="2026-07-06",
            horizon_end="2026-07-12",
            candidate_ids=cids,
        )
        compute_coherence(conn, contract.contract_id)
        # Eteint les risk_debts pour permettre le freeze
        for d in list_risk_debts(conn, status="open"):
            extinguish_risk_debt(conn, d.risk_debt_id, reason="test")
        run_p3_freeze(conn, contract.contract_id)
    return contract.contract_id, cids


@pytest.fixture
def db_frozen(tmp_db: Path, fixtures_v1_dir: Path) -> tuple[Path, str, list[str]]:
    """Base apres P3 freeze : 4 candidates en zone gelee + 4 OF en status='created'."""
    contract_id, cids = _frozen_state(tmp_db, fixtures_v1_dir)
    return tmp_db, contract_id, cids


# -----------------------------------------------------------------------
# Forme A : RETOUR_NEGOCIABLE
# -----------------------------------------------------------------------

def test_return_to_negociable_succeeds_when_of_created(
    db_frozen: tuple[Path, str, list[str]]
) -> None:
    db_path, _, cids = db_frozen
    cid = cids[0]
    with db_session(db_path) as conn:
        result = return_to_negociable(conn, cid, reason="renegotiate")
        zone = current_zone(conn, cid)
        of_row = conn.execute(
            "SELECT status FROM manufacturing_orders "
            "WHERE candidate_id = ? AND of_id = ?",
            (cid, result.cancelled_of_id),
        ).fetchone()
    assert zone == ZONE_NEGOCIABLE
    assert result.cancelled_of_id is not None
    assert of_row["status"] == "cancelled"


def test_return_refuses_if_candidate_not_in_gelee(
    db_frozen: tuple[Path, str, list[str]]
) -> None:
    db_path, _, cids = db_frozen
    cid = cids[0]
    with db_session(db_path) as conn:
        # On revient une 1ere fois -> negociable
        return_to_negociable(conn, cid, reason="first")
        # Une 2e tentative depuis negociable doit echouer
        with pytest.raises(ValueError, match="gelee"):
            return_to_negociable(conn, cid, reason="second")


def test_return_refuses_if_of_launched(
    db_frozen: tuple[Path, str, list[str]]
) -> None:
    """Si l'OF est launched, la forme A est interdite (utiliser fragment)."""
    db_path, _, cids = db_frozen
    cid = cids[0]
    with db_session(db_path) as conn:
        of_id = conn.execute(
            "SELECT of_id FROM manufacturing_orders WHERE candidate_id = ?",
            (cid,),
        ).fetchone()["of_id"]
        launch_of(conn, of_id)
        with pytest.raises(ValueError, match="forme B"):
            return_to_negociable(conn, cid, reason="too late")


def test_return_emits_events(db_frozen: tuple[Path, str, list[str]]) -> None:
    db_path, _, cids = db_frozen
    cid = cids[0]
    with db_session(db_path) as conn:
        result = return_to_negociable(conn, cid, reason="emit-test")
        cand_events = conn.execute(
            "SELECT event_type FROM event_store "
            "WHERE aggregate_type = 'candidate_order' AND aggregate_id = ?",
            (cid,),
        ).fetchall()
        of_events = conn.execute(
            "SELECT event_type FROM event_store "
            "WHERE aggregate_type = 'manufacturing_order' AND aggregate_id = ?",
            (result.cancelled_of_id,),
        ).fetchall()
    cand_types = [e["event_type"] for e in cand_events]
    of_types = [e["event_type"] for e in of_events]
    assert "OF_RETURNED_NEGOCIABLE" in cand_types
    assert "OF_CANCELLED" in of_types


# -----------------------------------------------------------------------
# Forme B : FRAGMENT
# -----------------------------------------------------------------------

def test_fragment_conserves_total_quantity(
    db_frozen: tuple[Path, str, list[str]]
) -> None:
    """fragment_qty + source_qty_after = source_qty_before (invariant doctrine)."""
    db_path, _, cids = db_frozen
    cid = cids[0]
    with db_session(db_path) as conn:
        of_id = conn.execute(
            "SELECT of_id FROM manufacturing_orders WHERE candidate_id = ?",
            (cid,),
        ).fetchone()["of_id"]
        launch_of(conn, of_id)
        source_qty_before = conn.execute(
            "SELECT quantity FROM manufacturing_orders WHERE of_id = ?",
            (of_id,),
        ).fetchone()["quantity"]

        result = fragment_of(
            conn, of_id, fragment_quantity=30.0, reason="renegotiate-partial"
        )
        source_qty_after = conn.execute(
            "SELECT quantity FROM manufacturing_orders WHERE of_id = ?",
            (of_id,),
        ).fetchone()["quantity"]
        fragment_qty = conn.execute(
            "SELECT quantity FROM manufacturing_orders WHERE of_id = ?",
            (result.fragment_of_id,),
        ).fetchone()["quantity"]
    assert source_qty_after + fragment_qty == source_qty_before
    assert result.source_quantity_after == source_qty_after
    assert result.fragment_quantity == fragment_qty


def test_fragment_creates_new_of_with_parent_lineage(
    db_frozen: tuple[Path, str, list[str]]
) -> None:
    db_path, _, cids = db_frozen
    cid = cids[0]
    with db_session(db_path) as conn:
        of_id = conn.execute(
            "SELECT of_id FROM manufacturing_orders WHERE candidate_id = ?",
            (cid,),
        ).fetchone()["of_id"]
        launch_of(conn, of_id)
        result = fragment_of(
            conn, of_id, fragment_quantity=30.0, reason="test"
        )
        fragment_row = conn.execute(
            "SELECT status, parent_of_id, candidate_id FROM manufacturing_orders "
            "WHERE of_id = ?",
            (result.fragment_of_id,),
        ).fetchone()
    assert fragment_row["status"] == "created"
    assert fragment_row["parent_of_id"] == of_id
    assert fragment_row["candidate_id"] == cid


def test_fragment_creates_pending_operations(
    db_frozen: tuple[Path, str, list[str]]
) -> None:
    """Le fragment doit avoir ses operations en status 'pending' (refait depuis 0)."""
    db_path, _, cids = db_frozen
    cid = cids[0]
    with db_session(db_path) as conn:
        of_id = conn.execute(
            "SELECT of_id FROM manufacturing_orders WHERE candidate_id = ?",
            (cid,),
        ).fetchone()["of_id"]
        launch_of(conn, of_id)
        result = fragment_of(conn, of_id, fragment_quantity=30.0, reason="x")
        ops = conn.execute(
            "SELECT status FROM order_operations WHERE of_id = ?",
            (result.fragment_of_id,),
        ).fetchall()
    assert len(ops) > 0
    assert all(op["status"] == "pending" for op in ops)


def test_fragment_refuses_if_of_not_launched(
    db_frozen: tuple[Path, str, list[str]]
) -> None:
    """Forme B interdite si OF en status='created' (utiliser forme A)."""
    db_path, _, cids = db_frozen
    cid = cids[0]
    with db_session(db_path) as conn:
        of_id = conn.execute(
            "SELECT of_id FROM manufacturing_orders WHERE candidate_id = ?",
            (cid,),
        ).fetchone()["of_id"]
        # OF en status='created' encore
        with pytest.raises(ValueError, match="launched"):
            fragment_of(conn, of_id, fragment_quantity=30.0, reason="test")


def test_fragment_refuses_invalid_quantity(
    db_frozen: tuple[Path, str, list[str]]
) -> None:
    db_path, _, cids = db_frozen
    cid = cids[0]
    with db_session(db_path) as conn:
        of_id = conn.execute(
            "SELECT of_id FROM manufacturing_orders WHERE candidate_id = ?",
            (cid,),
        ).fetchone()["of_id"]
        launch_of(conn, of_id)
        with pytest.raises(ValueError, match="strictement positif"):
            fragment_of(conn, of_id, fragment_quantity=0, reason="x")
        with pytest.raises(ValueError, match="strictement positif"):
            fragment_of(conn, of_id, fragment_quantity=-5, reason="x")
        with pytest.raises(ValueError, match="< quantite source"):
            fragment_of(conn, of_id, fragment_quantity=999, reason="x")


def test_fragment_emits_event(db_frozen: tuple[Path, str, list[str]]) -> None:
    db_path, _, cids = db_frozen
    cid = cids[0]
    with db_session(db_path) as conn:
        of_id = conn.execute(
            "SELECT of_id FROM manufacturing_orders WHERE candidate_id = ?",
            (cid,),
        ).fetchone()["of_id"]
        launch_of(conn, of_id)
        result = fragment_of(conn, of_id, fragment_quantity=30.0, reason="emit-test")
        events = conn.execute(
            "SELECT event_type, payload_json FROM event_store "
            "WHERE aggregate_type = 'manufacturing_order' AND aggregate_id = ?",
            (of_id,),
        ).fetchall()
    types = [e["event_type"] for e in events]
    assert "OF_FRAGMENTED" in types


# -----------------------------------------------------------------------
# Filiation (lineage)
# -----------------------------------------------------------------------

def test_lineage_returns_parent_and_children(
    db_frozen: tuple[Path, str, list[str]]
) -> None:
    db_path, _, cids = db_frozen
    cid = cids[0]
    with db_session(db_path) as conn:
        of_id = conn.execute(
            "SELECT of_id FROM manufacturing_orders WHERE candidate_id = ?",
            (cid,),
        ).fetchone()["of_id"]
        launch_of(conn, of_id)
        result = fragment_of(conn, of_id, fragment_quantity=30.0, reason="x")

        # Filiation depuis le fragment doit remonter au parent
        fragment_lineage = get_lineage(conn, result.fragment_of_id)
        # Filiation depuis le source doit descendre vers le fragment
        source_lineage = get_lineage(conn, of_id)
    fragment_ids = [n.of_id for n in fragment_lineage]
    source_ids = [n.of_id for n in source_lineage]
    assert of_id in fragment_ids
    assert result.fragment_of_id in fragment_ids
    assert result.fragment_of_id in source_ids


def test_lineage_returns_empty_for_unknown(
    db_frozen: tuple[Path, str, list[str]]
) -> None:
    db_path, _, _ = db_frozen
    with db_session(db_path) as conn:
        result = get_lineage(conn, "OF-INEXISTANT")
    assert result == []
