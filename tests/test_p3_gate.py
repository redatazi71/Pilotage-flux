"""Tests de la porte P3 (freeze d'un contrat de flux)."""

from pathlib import Path

import pytest

from pilotage_flux.aps import compute_candidates
from pilotage_flux.db import db_session
from pilotage_flux.flux import (
    compute_coherence,
    create_contract,
    fetch_contract,
    fetch_freeze_batch,
    get_batch_contracts,
    list_freeze_batches,
)
from pilotage_flux.gates import (
    DECISION_FREEZE,
    DECISION_RENEGOTIATE,
    evaluate_p3_for_contract,
    run_p2_on_libre_zone,
    run_p3_freeze,
)
from pilotage_flux.importers import import_referentials
from pilotage_flux.risk_debt import extinguish_risk_debt, list_risk_debts
from pilotage_flux.zones import ZONE_GELEE, ZONE_NEGOCIABLE


def _prepare_v1_with_contract(
    db_path: Path,
    fixtures_v1_dir: Path,
    *,
    extinguish_debts: bool = True,
    compute: bool = True,
    horizon: tuple[str, str] = ("2026-07-06", "2026-07-12"),
) -> str:
    """Helper : crée un contrat de flux complet, optionnellement éteint les debts."""
    with db_session(db_path) as conn:
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
            horizon_start=horizon[0],
            horizon_end=horizon[1],
            candidate_ids=cids,
        )
        if compute:
            compute_coherence(conn, contract.contract_id)
        if extinguish_debts:
            for d in list_risk_debts(conn, status="open"):
                extinguish_risk_debt(
                    conn, d.risk_debt_id, reason="test prerequisite"
                )
    return contract.contract_id


@pytest.fixture
def db_ready(tmp_db: Path, fixtures_v1_dir: Path) -> tuple[Path, str]:
    cid = _prepare_v1_with_contract(tmp_db, fixtures_v1_dir)
    return tmp_db, cid


def test_p3_dry_run_returns_4_criteria(db_ready: tuple[Path, str]) -> None:
    db_path, cid = db_ready
    with db_session(db_path) as conn:
        criteria = evaluate_p3_for_contract(conn, cid)
    rule_ids = {c.rule_id for c in criteria}
    assert rule_ids == {"R-P3-01", "R-P3-02", "R-P3-03", "R-P3-04"}


def test_p3_freeze_all_pass(db_ready: tuple[Path, str]) -> None:
    """Avec coherence OK + debts eteintes, P3 doit FREEZE."""
    db_path, cid = db_ready
    with db_session(db_path) as conn:
        result = run_p3_freeze(conn, cid)
        contract = fetch_contract(conn, cid)
    assert result.decision == DECISION_FREEZE
    assert result.batch_id is not None
    assert result.batch_id.startswith("FZ-")
    assert contract.status == "frozen"


def test_p3_freeze_moves_candidates_to_gelee_zone(db_ready: tuple[Path, str]) -> None:
    db_path, cid = db_ready
    with db_session(db_path) as conn:
        run_p3_freeze(conn, cid)
        zones = conn.execute(
            "SELECT DISTINCT zone FROM candidate_orders"
        ).fetchall()
    assert len(zones) == 1
    assert zones[0]["zone"] == ZONE_GELEE


def test_p3_creates_freeze_batch_with_snapshot(db_ready: tuple[Path, str]) -> None:
    db_path, cid = db_ready
    with db_session(db_path) as conn:
        result = run_p3_freeze(conn, cid)
        batch = fetch_freeze_batch(conn, result.batch_id)
        contracts = get_batch_contracts(conn, result.batch_id)
    assert batch is not None
    assert batch.decision == DECISION_FREEZE
    assert batch.contract_count == 1
    assert batch.candidate_count == 4
    assert batch.total_quantity == 300.0
    assert len(contracts) == 1
    assert contracts[0].contract_id == cid
    assert contracts[0].version == 1


def test_p3_blocks_when_contract_not_coherent(
    tmp_db: Path, fixtures_v1_dir: Path
) -> None:
    """Sans compute_coherence prealable, le contrat n'est pas marque coherent."""
    cid = _prepare_v1_with_contract(
        tmp_db, fixtures_v1_dir, compute=False, extinguish_debts=True
    )
    with db_session(tmp_db) as conn:
        result = run_p3_freeze(conn, cid)
        contract = fetch_contract(conn, cid)
    assert result.decision == DECISION_RENEGOTIATE
    assert result.batch_id is None
    blocked_r1 = next(c for c in result.criteria if c.rule_id == "R-P3-01")
    assert blocked_r1.outcome == "BLOCK"
    # Le contrat reste en draft (pas frozen)
    assert contract.status == "draft"


def test_p3_blocks_when_open_risk_debt(tmp_db: Path, fixtures_v1_dir: Path) -> None:
    """Si une risk_debt est ouverte, P3 doit BLOCK sur R-P3-03."""
    cid = _prepare_v1_with_contract(
        tmp_db, fixtures_v1_dir, extinguish_debts=False
    )
    with db_session(tmp_db) as conn:
        result = run_p3_freeze(conn, cid)
    assert result.decision == DECISION_RENEGOTIATE
    blocked = next(c for c in result.criteria if c.rule_id == "R-P3-03")
    assert blocked.outcome == "BLOCK"


def test_p3_blocks_if_candidate_not_in_negociable(
    db_ready: tuple[Path, str]
) -> None:
    """Si un candidate revient en libre apres P2, P3 doit BLOCK sur R-P3-02."""
    db_path, cid = db_ready
    with db_session(db_path) as conn:
        # Triche : force un candidate du contrat hors de negociable
        conn.execute(
            "UPDATE candidate_orders SET zone = 'libre' "
            "WHERE candidate_id = (SELECT candidate_id FROM flux_contract_links "
            "WHERE contract_id = ? LIMIT 1)",
            (cid,),
        )
        result = run_p3_freeze(conn, cid)
    assert result.decision == DECISION_RENEGOTIATE
    blocked = next(c for c in result.criteria if c.rule_id == "R-P3-02")
    assert blocked.outcome == "BLOCK"


def test_p3_blocks_if_overlapping_freeze_exists(
    tmp_db: Path, fixtures_v1_dir: Path
) -> None:
    """Un 2e contrat dont l'horizon chevauche un freeze existant doit BLOCK R-P3-04."""
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
        # Premier contrat : freeze OK
        c1 = create_contract(
            conn,
            horizon_label="W27",
            horizon_start="2026-07-06",
            horizon_end="2026-07-08",
            candidate_ids=[cids[0]],
        )
        compute_coherence(conn, c1.contract_id)
        for d in list_risk_debts(conn, status="open", candidate_id=cids[0]):
            extinguish_risk_debt(conn, d.risk_debt_id, reason="test")
        r1 = run_p3_freeze(conn, c1.contract_id)
        assert r1.decision == DECISION_FREEZE

        # 2e contrat : horizon chevauchant
        c2 = create_contract(
            conn,
            horizon_label="W27b",
            horizon_start="2026-07-07",  # chevauche
            horizon_end="2026-07-09",
            candidate_ids=[cids[1]],
        )
        compute_coherence(conn, c2.contract_id)
        for d in list_risk_debts(conn, status="open", candidate_id=cids[1]):
            extinguish_risk_debt(conn, d.risk_debt_id, reason="test")
        r2 = run_p3_freeze(conn, c2.contract_id)

    assert r2.decision == DECISION_RENEGOTIATE
    overlap = next(c for c in r2.criteria if c.rule_id == "R-P3-04")
    assert overlap.outcome == "BLOCK"


def test_freeze_batch_is_immutable_snapshot(db_ready: tuple[Path, str]) -> None:
    """Après le freeze, modifier le contrat ne touche pas la tranche figée."""
    from pilotage_flux.flux import remove_candidate_from_contract

    db_path, cid = db_ready
    with db_session(db_path) as conn:
        result = run_p3_freeze(conn, cid)
        batch_id = result.batch_id
        # Les contracts en zone gelee ne peuvent pas être modifiés
        # (les candidates sont en zone gelee donc plus en negociable)
        with pytest.raises(ValueError):
            remove_candidate_from_contract(conn, cid, "CND-0001")
        # La tranche reste intacte
        batch_after = fetch_freeze_batch(conn, batch_id)
    assert batch_after is not None
    assert batch_after.candidate_count == 4
    assert batch_after.total_quantity == 300.0


def test_p3_decision_recorded_in_gate_decisions_v1(db_ready: tuple[Path, str]) -> None:
    db_path, cid = db_ready
    with db_session(db_path) as conn:
        run_p3_freeze(conn, cid)
        row = conn.execute(
            "SELECT decision FROM gate_decisions_v1 "
            "WHERE subject_id = ? AND gate = 'P3'",
            (cid,),
        ).fetchone()
    assert row is not None
    assert row["decision"] == DECISION_FREEZE


def test_p3_emits_event_on_freeze(db_ready: tuple[Path, str]) -> None:
    db_path, cid = db_ready
    with db_session(db_path) as conn:
        run_p3_freeze(conn, cid)
        events = conn.execute(
            """
            SELECT event_type, payload_json FROM event_store
            WHERE aggregate_type = 'flux_contract' AND aggregate_id = ?
            """,
            (cid,),
        ).fetchall()
    assert len(events) == 1
    assert events[0]["event_type"] == "GATE_DECISION"
    assert "FREEZE" in events[0]["payload_json"]
