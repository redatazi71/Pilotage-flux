"""Tests de la cohérence des contrats de flux."""

from pathlib import Path

import pytest

from pilotage_flux.aps import compute_candidates
from pilotage_flux.db import db_session
from pilotage_flux.flux import (
    compute_coherence,
    create_contract,
    fetch_contract,
)
from pilotage_flux.gates import run_p2_on_libre_zone
from pilotage_flux.importers import import_referentials


@pytest.fixture
def db_with_contract(tmp_db: Path, fixtures_v1_dir: Path) -> tuple[Path, str]:
    """Base avec 1 contrat regroupant les 4 candidates négociables."""
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
            horizon_start="2026-07-06",
            horizon_end="2026-07-12",
            candidate_ids=cids,
        )
    return tmp_db, contract.contract_id


def test_coherence_runs_workstation_load_checks(
    db_with_contract: tuple[Path, str]
) -> None:
    db_path, cid = db_with_contract
    with db_session(db_path) as conn:
        report = compute_coherence(conn, cid)
    # Pour V1 fixtures : SEMI-1 sur WS-1, ART-A sur WS-2 + WS-3
    workstations = {c.workstation_id for c in report.checks if c.workstation_id}
    assert workstations == {"WS-1", "WS-2", "WS-3"}


def test_coherence_marks_contract_as_coherent_when_load_fits(
    db_with_contract: tuple[Path, str]
) -> None:
    """Sur une semaine entière, la charge V1 fixtures tient largement."""
    db_path, cid = db_with_contract
    with db_session(db_path) as conn:
        report = compute_coherence(conn, cid)
        contract = fetch_contract(conn, cid)
    # WS-1 : (100 + 50) × 2.0 = 300 min
    # WS-2 : (100 + 50) × 3.0 = 450 min
    # WS-3 : (100 + 50) × 1.2 = 180 min
    # Capacité horizon (5 jours × 480 × factor)
    # WS-2 : 5 × 480 × 0.80 = 1920 min >> 450 → OK
    assert report.overall_ok is True
    assert contract.status == "coherent"


def test_coherence_detects_overload_on_short_horizon(
    tmp_db: Path, fixtures_v1_dir: Path
) -> None:
    """Sur un horizon 1 jour, WS-2 doit être en surcharge."""
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
            horizon_label="1d",
            horizon_start="2026-07-06",  # un seul lundi
            horizon_end="2026-07-06",
            candidate_ids=cids,
        )
        report = compute_coherence(conn, contract.contract_id)
        c = fetch_contract(conn, contract.contract_id)
    # WS-2 : capacité 1j = 480 × 0.80 = 384 min. Charge = 450 min → surcharge.
    violations = [v for v in report.violations if v.workstation_id == "WS-2"]
    assert len(violations) == 1
    assert report.overall_ok is False
    assert c.status == "incoherent"


def test_coherence_writes_checks_to_table(
    db_with_contract: tuple[Path, str]
) -> None:
    db_path, cid = db_with_contract
    with db_session(db_path) as conn:
        compute_coherence(conn, cid)
        rows = conn.execute(
            "SELECT COUNT(*) AS n FROM flux_coherence_checks WHERE contract_id = ?",
            (cid,),
        ).fetchone()
    # 3 workstations + 1 takt_vs_bottleneck
    assert int(rows["n"]) == 4


def test_coherence_recomputes_purges_previous_checks(
    db_with_contract: tuple[Path, str]
) -> None:
    db_path, cid = db_with_contract
    with db_session(db_path) as conn:
        compute_coherence(conn, cid)
        first_count = conn.execute(
            "SELECT COUNT(*) AS n FROM flux_coherence_checks WHERE contract_id = ?",
            (cid,),
        ).fetchone()["n"]
        compute_coherence(conn, cid)
        second_count = conn.execute(
            "SELECT COUNT(*) AS n FROM flux_coherence_checks WHERE contract_id = ?",
            (cid,),
        ).fetchone()["n"]
    assert first_count == second_count == 4
