"""Tests du lissage des lancements d'un contrat de flux."""

from pathlib import Path

import pytest

from pilotage_flux.aps import compute_candidates
from pilotage_flux.db import db_session
from pilotage_flux.flux import (
    compute_smoothing,
    create_contract,
    get_smoothed_launches,
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
            horizon_label="2026-W27",
            horizon_start="2026-07-06T00:00:00",
            horizon_end="2026-07-13T00:00:00",  # exactly 7 jours
            candidate_ids=cids,
        )
    return tmp_db, contract.contract_id


def test_smoothing_produces_one_launch_per_candidate(
    db_with_contract: tuple[Path, str]
) -> None:
    db_path, cid = db_with_contract
    with db_session(db_path) as conn:
        launches = compute_smoothing(conn, cid)
    assert len(launches) == 4


def test_smoothing_first_offset_is_zero(
    db_with_contract: tuple[Path, str]
) -> None:
    """Le premier candidate démarre à offset_minutes = 0."""
    db_path, cid = db_with_contract
    with db_session(db_path) as conn:
        launches = compute_smoothing(conn, cid)
    assert launches[0].offset_minutes == 0


def test_smoothing_offsets_are_monotonically_increasing(
    db_with_contract: tuple[Path, str]
) -> None:
    db_path, cid = db_with_contract
    with db_session(db_path) as conn:
        launches = compute_smoothing(conn, cid)
    offsets = [l.offset_minutes for l in launches]
    assert offsets == sorted(offsets)


def test_smoothing_offsets_proportional_to_cumulative_quantity(
    db_with_contract: tuple[Path, str]
) -> None:
    """Quantités fixtures V1 : ART-A 100, SEMI-1 100, ART-A 50, SEMI-1 50, total 300.
    Cumuls à l'entrée du candidate i : 0, 100, 200, 250.
    Sur 7 jours = 10080 minutes, offsets attendus : 0, 3360, 6720, 8400.
    """
    db_path, cid = db_with_contract
    with db_session(db_path) as conn:
        launches = compute_smoothing(conn, cid)
    offsets = [l.offset_minutes for l in launches]
    # Tolérance ±2 min pour arrondi
    assert offsets[0] == 0
    assert 3358 <= offsets[1] <= 3362
    assert 6718 <= offsets[2] <= 6722
    assert 8398 <= offsets[3] <= 8402


def test_smoothing_persisted_and_retrievable(
    db_with_contract: tuple[Path, str]
) -> None:
    db_path, cid = db_with_contract
    with db_session(db_path) as conn:
        compute_smoothing(conn, cid)
        retrieved = get_smoothed_launches(conn, cid)
    assert len(retrieved) == 4
    assert retrieved[0].offset_minutes == 0


def test_smoothing_recompute_purges_previous(
    db_with_contract: tuple[Path, str]
) -> None:
    db_path, cid = db_with_contract
    with db_session(db_path) as conn:
        compute_smoothing(conn, cid)
        n1 = conn.execute(
            "SELECT COUNT(*) AS n FROM flux_smoothed_launches WHERE contract_id = ?",
            (cid,),
        ).fetchone()["n"]
        compute_smoothing(conn, cid)
        n2 = conn.execute(
            "SELECT COUNT(*) AS n FROM flux_smoothed_launches WHERE contract_id = ?",
            (cid,),
        ).fetchone()["n"]
    assert n1 == n2 == 4
