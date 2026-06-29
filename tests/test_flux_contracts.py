"""Tests des contrats de flux versionnés."""

from pathlib import Path

import pytest

from pilotage_flux.aps import compute_candidates
from pilotage_flux.db import db_session
from pilotage_flux.flux import (
    add_candidate_to_contract,
    create_contract,
    fetch_contract,
    fetch_version,
    get_candidates_in_version,
    list_contracts,
    remove_candidate_from_contract,
)
from pilotage_flux.gates import run_p2_on_libre_zone
from pilotage_flux.importers import import_referentials


@pytest.fixture
def db_with_negociable(tmp_db: Path, fixtures_v1_dir: Path) -> Path:
    """Base avec 4 candidates en zone négociable (P2 PASS_WITH_RISK)."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        compute_candidates(conn)
        run_p2_on_libre_zone(conn)
    return tmp_db


def _two_first_ids(db_path: Path) -> tuple[str, str]:
    with db_session(db_path) as conn:
        rows = conn.execute(
            "SELECT candidate_id FROM candidate_orders ORDER BY candidate_id LIMIT 2"
        ).fetchall()
    return rows[0]["candidate_id"], rows[1]["candidate_id"]


def test_create_contract_makes_v1(db_with_negociable: Path) -> None:
    a, b = _two_first_ids(db_with_negociable)
    with db_session(db_with_negociable) as conn:
        contract = create_contract(
            conn,
            horizon_label="2026-W27",
            horizon_start="2026-07-06",
            horizon_end="2026-07-12",
            candidate_ids=[a, b],
        )
        ver = fetch_version(conn, contract.contract_id, 1)
        cands = get_candidates_in_version(conn, contract.contract_id, 1)
    assert contract.contract_id == "FX-0001"
    assert contract.current_version == 1
    assert contract.status == "draft"
    assert ver.total_quantity > 0
    assert ver.takt_target_min is not None
    assert ver.wip_target is not None
    assert len(cands) == 2


def test_create_refuses_empty_candidate_list(db_with_negociable: Path) -> None:
    with db_session(db_with_negociable) as conn:
        with pytest.raises(ValueError, match="au moins un"):
            create_contract(
                conn,
                horizon_label="X", horizon_start="2026-07-06", horizon_end="2026-07-12",
                candidate_ids=[],
            )


def test_create_refuses_unknown_candidate(db_with_negociable: Path) -> None:
    with db_session(db_with_negociable) as conn:
        with pytest.raises(ValueError, match="inconnu"):
            create_contract(
                conn,
                horizon_label="X", horizon_start="2026-07-06", horizon_end="2026-07-12",
                candidate_ids=["CND-9999"],
            )


def test_create_refuses_candidate_not_in_negociable(
    tmp_db: Path, fixtures_v1_dir: Path
) -> None:
    """Candidates encore en zone libre ne peuvent pas entrer dans un contrat."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        compute_candidates(conn)
        # Candidates en zone libre (P2 pas exécuté)
        cid = conn.execute(
            "SELECT candidate_id FROM candidate_orders LIMIT 1"
        ).fetchone()["candidate_id"]
        with pytest.raises(ValueError, match="zone 'negociable'"):
            create_contract(
                conn,
                horizon_label="X", horizon_start="2026-07-06", horizon_end="2026-07-12",
                candidate_ids=[cid],
            )


def test_candidate_cannot_be_in_two_active_contracts(
    db_with_negociable: Path,
) -> None:
    a, b = _two_first_ids(db_with_negociable)
    with db_session(db_with_negociable) as conn:
        create_contract(
            conn,
            horizon_label="W27", horizon_start="2026-07-06", horizon_end="2026-07-12",
            candidate_ids=[a],
        )
        with pytest.raises(ValueError, match="déjà dans"):
            create_contract(
                conn,
                horizon_label="W28", horizon_start="2026-07-13", horizon_end="2026-07-19",
                candidate_ids=[a, b],
            )


def test_add_candidate_creates_new_version(db_with_negociable: Path) -> None:
    a, b = _two_first_ids(db_with_negociable)
    with db_session(db_with_negociable) as conn:
        contract = create_contract(
            conn,
            horizon_label="W27", horizon_start="2026-07-06", horizon_end="2026-07-12",
            candidate_ids=[a],
        )
        new_v = add_candidate_to_contract(conn, contract.contract_id, b)
        updated = fetch_contract(conn, contract.contract_id)
        v1_cands = get_candidates_in_version(conn, contract.contract_id, 1)
        v2_cands = get_candidates_in_version(conn, contract.contract_id, 2)
    assert new_v == 2
    assert updated.current_version == 2
    # v1 garde son contenu original (versioning immuable)
    assert len(v1_cands) == 1
    assert v1_cands[0]["candidate_id"] == a
    # v2 contient les 2
    assert len(v2_cands) == 2
    assert {c["candidate_id"] for c in v2_cands} == {a, b}


def test_add_candidate_already_in_contract_raises(
    db_with_negociable: Path,
) -> None:
    a, _ = _two_first_ids(db_with_negociable)
    with db_session(db_with_negociable) as conn:
        contract = create_contract(
            conn,
            horizon_label="W27", horizon_start="2026-07-06", horizon_end="2026-07-12",
            candidate_ids=[a],
        )
        with pytest.raises(ValueError, match="déjà dans le contrat"):
            add_candidate_to_contract(conn, contract.contract_id, a)


def test_remove_candidate_creates_new_version(db_with_negociable: Path) -> None:
    a, b = _two_first_ids(db_with_negociable)
    with db_session(db_with_negociable) as conn:
        contract = create_contract(
            conn,
            horizon_label="W27", horizon_start="2026-07-06", horizon_end="2026-07-12",
            candidate_ids=[a, b],
        )
        new_v = remove_candidate_from_contract(conn, contract.contract_id, a)
        v2_cands = get_candidates_in_version(conn, contract.contract_id, 2)
    assert new_v == 2
    assert len(v2_cands) == 1
    assert v2_cands[0]["candidate_id"] == b


def test_cannot_remove_last_candidate(db_with_negociable: Path) -> None:
    a, _ = _two_first_ids(db_with_negociable)
    with db_session(db_with_negociable) as conn:
        contract = create_contract(
            conn,
            horizon_label="W27", horizon_start="2026-07-06", horizon_end="2026-07-12",
            candidate_ids=[a],
        )
        with pytest.raises(ValueError, match="dernier"):
            remove_candidate_from_contract(conn, contract.contract_id, a)


def test_list_contracts_filters_by_status(db_with_negociable: Path) -> None:
    a, b = _two_first_ids(db_with_negociable)
    with db_session(db_with_negociable) as conn:
        create_contract(
            conn,
            horizon_label="W27", horizon_start="2026-07-06", horizon_end="2026-07-12",
            candidate_ids=[a],
        )
        create_contract(
            conn,
            horizon_label="W28", horizon_start="2026-07-13", horizon_end="2026-07-19",
            candidate_ids=[b],
        )
        drafts = list_contracts(conn, status="draft")
    assert len(drafts) == 2
    assert {c.contract_id for c in drafts} == {"FX-0001", "FX-0002"}
