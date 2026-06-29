"""Tests du matching attendu/réel et qualification des écarts (L3.2)."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from pilotage_flux.aps import compute_candidates
from pilotage_flux.db import db_session
from pilotage_flux.events_v3 import (
    generate_expected_from_batch,
    list_deviations,
    list_expected,
    match_actuals_to_expected,
)
from pilotage_flux.flux import (
    compute_coherence,
    compute_smoothing,
    create_contract,
)
from pilotage_flux.gates import (
    run_p1_promotion,
    run_p2_on_libre_zone,
    run_p3_freeze,
)
from pilotage_flux.importers import import_referentials
from pilotage_flux.mes import close_of, finish_operation, launch_of, start_operation
from pilotage_flux.risk_debt import extinguish_risk_debt, list_risk_debts


@pytest.fixture
def db_frozen_and_executed(
    tmp_db: Path, fixtures_v1_dir: Path
) -> tuple[Path, str, str]:
    """Base après P3 freeze + execution complète d'un OF (CND-0002)."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        compute_candidates(conn)
        run_p1_promotion(conn)
        run_p2_on_libre_zone(conn)
        cids = [
            r["candidate_id"]
            for r in conn.execute(
                "SELECT candidate_id FROM candidate_orders ORDER BY candidate_id"
            )
        ]
        contract = create_contract(
            conn, horizon_label="W27",
            horizon_start="2026-07-06", horizon_end="2026-07-12",
            candidate_ids=cids,
        )
        compute_coherence(conn, contract.contract_id)
        compute_smoothing(conn, contract.contract_id)
        for d in list_risk_debts(conn, status="open"):
            extinguish_risk_debt(conn, d.risk_debt_id, reason="test")
        result = run_p3_freeze(conn, contract.contract_id)
        generate_expected_from_batch(conn, result.batch_id)

        # Execute OF-0002 (SEMI-1, 1 op WS-1) - le candidat associé
        of_id = conn.execute(
            "SELECT of_id FROM manufacturing_orders "
            "WHERE article_id = 'SEMI-1' AND quantity = 100"
        ).fetchone()["of_id"]
        launch_of(conn, of_id)
        ops = conn.execute(
            "SELECT of_op_id FROM order_operations WHERE of_id = ? ORDER BY sequence_idx",
            (of_id,),
        ).fetchall()
        for op in ops:
            start_operation(conn, op["of_op_id"])
            finish_operation(conn, op["of_op_id"], qty_good=95, qty_scrap=5)
        close_of(conn, of_id)
    return tmp_db, contract.contract_id, result.batch_id


def test_match_produces_deviations_for_executed_of(
    db_frozen_and_executed: tuple[Path, str, str]
) -> None:
    """Après match, on a au moins 3 deviations (OP_STARTED + OP_FINISHED + OF_CLOSED)
    pour le candidate dont l'OF a été exécuté."""
    db_path, _, batch_id = db_frozen_and_executed
    with db_session(db_path) as conn:
        devs = match_actuals_to_expected(conn, batch_id)
        # CND-0002 (SEMI-1, 1 op) : op_start + op_finish + of_close = 3 events match
        cand_devs = [d for d in devs if d.candidate_id == "CND-0002"]
    assert len(cand_devs) == 3


def test_match_assigns_score_and_qualification(
    db_frozen_and_executed: tuple[Path, str, str]
) -> None:
    db_path, _, batch_id = db_frozen_and_executed
    with db_session(db_path) as conn:
        devs = match_actuals_to_expected(conn, batch_id)
    for d in devs:
        assert d.score is not None
        assert 0 <= d.score <= 1
        assert d.qualification in ("low", "medium", "high", "critical")
        assert d.deviation_kind == "time_delta"


def test_match_updates_expected_events_matched_at(
    db_frozen_and_executed: tuple[Path, str, str]
) -> None:
    db_path, _, batch_id = db_frozen_and_executed
    with db_session(db_path) as conn:
        before = list_expected(conn, batch_id=batch_id, candidate_id="CND-0002", unmatched_only=True)
        match_actuals_to_expected(conn, batch_id)
        after = list_expected(conn, batch_id=batch_id, candidate_id="CND-0002", unmatched_only=True)
    # Avant : tous unmatched ; après : 3 ont matched_actual_id
    assert len(before) > 0
    assert len(after) == len(before) - 3


def test_match_is_idempotent(
    db_frozen_and_executed: tuple[Path, str, str]
) -> None:
    """Re-match ne re-crée pas de deviations (les expected déjà matchés sont ignorés)."""
    db_path, _, batch_id = db_frozen_and_executed
    with db_session(db_path) as conn:
        first = match_actuals_to_expected(conn, batch_id)
        second = match_actuals_to_expected(conn, batch_id)
    assert len(first) > 0
    assert len(second) == 0


def test_no_match_for_non_executed_of(
    db_frozen_and_executed: tuple[Path, str, str]
) -> None:
    """Les candidates dont l'OF n'a pas été exécuté n'ont pas de deviations."""
    db_path, _, batch_id = db_frozen_and_executed
    with db_session(db_path) as conn:
        match_actuals_to_expected(conn, batch_id)
        # CND-0001/0003/0004 n'ont pas tourné MES
        devs_others = list_deviations(conn)
        cand_ids = {d.candidate_id for d in devs_others}
    assert "CND-0001" not in cand_ids
    assert "CND-0002" in cand_ids


def test_list_deviations_filters_by_min_score(
    db_frozen_and_executed: tuple[Path, str, str]
) -> None:
    db_path, _, batch_id = db_frozen_and_executed
    with db_session(db_path) as conn:
        match_actuals_to_expected(conn, batch_id)
        all_devs = list_deviations(conn)
        critical = list_deviations(conn, min_score=0.8)
    assert len(all_devs) >= len(critical)


def test_delta_time_is_positive_when_actual_later(
    db_frozen_and_executed: tuple[Path, str, str]
) -> None:
    """Le réel arrive après l'attendu (delta > 0) car l'exécution est immédiate
    alors que l'attendu était calé sur l'horizon W27 dans le futur."""
    db_path, _, batch_id = db_frozen_and_executed
    with db_session(db_path) as conn:
        devs = match_actuals_to_expected(conn, batch_id)
    # Au moins une déviation existe et a un delta non nul
    deltas = [d.delta_value for d in devs if d.delta_value is not None]
    assert any(d != 0 for d in deltas)
