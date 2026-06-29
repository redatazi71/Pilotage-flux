"""Tests directs du module flux.freeze (sans passer par la porte P3)."""

from pathlib import Path

import pytest

from pilotage_flux.aps import compute_candidates
from pilotage_flux.db import db_session
from pilotage_flux.flux import (
    create_contract,
    create_freeze_batch,
    fetch_freeze_batch,
    get_batch_contracts,
    list_freeze_batches,
    overlapping_freeze_batches,
)
from pilotage_flux.gates import run_p2_on_libre_zone
from pilotage_flux.importers import import_referentials


@pytest.fixture
def db_with_contract(tmp_db: Path, fixtures_v1_dir: Path) -> tuple[Path, str]:
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        compute_candidates(conn)
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
    return tmp_db, contract.contract_id


def test_create_freeze_batch_requires_at_least_one_contract(
    db_with_contract: tuple[Path, str]
) -> None:
    db_path, _ = db_with_contract
    with db_session(db_path) as conn:
        with pytest.raises(ValueError, match="au moins un"):
            create_freeze_batch(
                conn,
                contracts=[],
                horizon_start="2026-07-06",
                horizon_end="2026-07-12",
                decision="FREEZE",
            )


def test_create_freeze_batch_rejects_unknown_version(
    db_with_contract: tuple[Path, str]
) -> None:
    db_path, cid = db_with_contract
    with db_session(db_path) as conn:
        with pytest.raises(ValueError, match="introuvable"):
            create_freeze_batch(
                conn,
                contracts=[(cid, 999)],
                horizon_start="2026-07-06",
                horizon_end="2026-07-12",
                decision="FREEZE",
            )


def test_create_freeze_batch_aggregates_totals(
    db_with_contract: tuple[Path, str]
) -> None:
    db_path, cid = db_with_contract
    with db_session(db_path) as conn:
        batch = create_freeze_batch(
            conn,
            contracts=[(cid, 1)],
            horizon_start="2026-07-06",
            horizon_end="2026-07-12",
            decision="FREEZE",
            explanation="test",
        )
    assert batch.batch_id == "FZ-0001"
    assert batch.contract_count == 1
    assert batch.candidate_count == 4
    assert batch.total_quantity == 300.0


def test_overlapping_freeze_batches_detects_overlap(
    db_with_contract: tuple[Path, str]
) -> None:
    db_path, cid = db_with_contract
    with db_session(db_path) as conn:
        create_freeze_batch(
            conn,
            contracts=[(cid, 1)],
            horizon_start="2026-07-06",
            horizon_end="2026-07-12",
            decision="FREEZE",
        )
        # Plage chevauchante au début
        overlap1 = overlapping_freeze_batches(conn, "2026-07-04", "2026-07-07")
        # Plage chevauchante à la fin
        overlap2 = overlapping_freeze_batches(conn, "2026-07-10", "2026-07-15")
        # Plage entièrement avant
        before = overlapping_freeze_batches(conn, "2026-06-25", "2026-07-05")
        # Plage entièrement après
        after = overlapping_freeze_batches(conn, "2026-07-13", "2026-07-20")
    assert len(overlap1) == 1
    assert len(overlap2) == 1
    assert before == []
    assert after == []


def test_list_freeze_batches_filters_by_status(
    db_with_contract: tuple[Path, str]
) -> None:
    db_path, cid = db_with_contract
    with db_session(db_path) as conn:
        create_freeze_batch(
            conn,
            contracts=[(cid, 1)],
            horizon_start="2026-07-06",
            horizon_end="2026-07-12",
            decision="FREEZE",
        )
        frozen = list_freeze_batches(conn, status="frozen")
        revoked = list_freeze_batches(conn, status="revoked")
    assert len(frozen) == 1
    assert len(revoked) == 0


def test_get_batch_contracts_returns_snapshot(
    db_with_contract: tuple[Path, str]
) -> None:
    db_path, cid = db_with_contract
    with db_session(db_path) as conn:
        batch = create_freeze_batch(
            conn,
            contracts=[(cid, 1)],
            horizon_start="2026-07-06",
            horizon_end="2026-07-12",
            decision="FREEZE",
        )
        contracts = get_batch_contracts(conn, batch.batch_id)
    assert len(contracts) == 1
    assert contracts[0].contract_id == cid
    assert contracts[0].version == 1
