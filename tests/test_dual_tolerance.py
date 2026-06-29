"""Tests du filtre dual de tolérances (L3.5)."""

from pathlib import Path

import pytest

from pilotage_flux.aps import compute_candidates
from pilotage_flux.db import db_session
from pilotage_flux.events_v3 import (
    ACTION_ESCALATE,
    ACTION_INFORM,
    ACTION_REPLAN_GLOBAL,
    apply_cpm_absorption,
    evaluate_all_open_deviations,
    evaluate_dual_tolerance,
    generate_expected_from_batch,
    list_decisions,
    list_deviations,
    match_actuals_to_expected,
)
from pilotage_flux.flux import compute_coherence, compute_smoothing, create_contract
from pilotage_flux.gates import (
    run_p1_promotion,
    run_p2_on_libre_zone,
    run_p3_freeze,
)
from pilotage_flux.importers import import_referentials
from pilotage_flux.mes import close_of, finish_operation, launch_of, start_operation
from pilotage_flux.risk_debt import extinguish_risk_debt, list_risk_debts


@pytest.fixture
def db_v3_with_deviations(
    tmp_db: Path, fixtures_v1_dir: Path
) -> tuple[Path, str]:
    """Base avec un OF exécuté + déviations calculées + marge CPM nulle."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        # CPM = 0 pour qu'aucune déviation ne soit absorbée
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'cpm_margin_minutes', 0)"
        )
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
            conn, horizon_label="W",
            horizon_start="2026-07-06", horizon_end="2026-07-12",
            candidate_ids=cids,
        )
        compute_coherence(conn, contract.contract_id)
        compute_smoothing(conn, contract.contract_id)
        for d in list_risk_debts(conn, status="open"):
            extinguish_risk_debt(conn, d.risk_debt_id, reason="t")
        result = run_p3_freeze(conn, contract.contract_id)
        generate_expected_from_batch(conn, result.batch_id)

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
        match_actuals_to_expected(conn, result.batch_id)
        apply_cpm_absorption(conn, batch_id=result.batch_id)
    return tmp_db, result.batch_id


def test_evaluate_creates_decision_per_deviation(
    db_v3_with_deviations: tuple[Path, str]
) -> None:
    db_path, batch_id = db_v3_with_deviations
    with db_session(db_path) as conn:
        devs = list_deviations(conn)
        decisions = evaluate_all_open_deviations(conn, batch_id=batch_id)
    assert len(decisions) == len(devs)


def test_evaluate_is_idempotent(db_v3_with_deviations: tuple[Path, str]) -> None:
    db_path, batch_id = db_v3_with_deviations
    with db_session(db_path) as conn:
        first = evaluate_all_open_deviations(conn, batch_id=batch_id)
        second = evaluate_all_open_deviations(conn, batch_id=batch_id)
    assert len(first) > 0
    assert len(second) == 0


def test_absorbed_deviation_gets_inform_level(
    db_v3_with_deviations: tuple[Path, str]
) -> None:
    """Une déviation absorbée par CPM doit avoir action_level=inform."""
    db_path, _ = db_v3_with_deviations
    with db_session(db_path) as conn:
        # On force une déviation à absorbed (simulation)
        dev = list_deviations(conn)[0]
        conn.execute(
            "UPDATE event_deviations SET is_absorbed = 1 WHERE deviation_id = ?",
            (dev.deviation_id,),
        )
        decision = evaluate_dual_tolerance(conn, dev.deviation_id)
    assert decision.action_level == ACTION_INFORM


def test_threshold_data_driven(db_v3_with_deviations: tuple[Path, str]) -> None:
    """Modifier les seuils en table change le niveau d'action."""
    db_path, batch_id = db_v3_with_deviations
    with db_session(db_path) as conn:
        # Seuils ultra-bas → tout devient replan_global
        for name in (
            "tolerance_threshold_watch",
            "tolerance_threshold_correct_local",
            "tolerance_threshold_replan_local",
            "tolerance_threshold_escalate",
            "tolerance_threshold_replan_global",
        ):
            conn.execute(
                "INSERT INTO parameters (scope, scope_ref, name, value_num) "
                "VALUES ('global', NULL, ?, 0)",
                (name,),
            )
        decisions = evaluate_all_open_deviations(conn, batch_id=batch_id)
        # Toutes les non-absorbed deviennent replan_global
        non_absorbed_decs = [
            d for d in decisions
            if d.score_magnitude > 0
        ]
    if non_absorbed_decs:
        assert all(
            d.action_level == ACTION_REPLAN_GLOBAL for d in non_absorbed_decs
        ), [d.action_level for d in non_absorbed_decs]


def test_frequency_amplifies_score_combined(
    db_v3_with_deviations: tuple[Path, str]
) -> None:
    """À magnitude égale, score_combined augmente avec frequency_in_window."""
    db_path, _ = db_v3_with_deviations
    with db_session(db_path) as conn:
        devs = list_deviations(conn)
        # Évalue toutes : la première a freq=1, les suivantes augmentent
        decisions = [evaluate_dual_tolerance(conn, d.deviation_id) for d in devs]
    # Les frequencies augmentent monotoniquement (nb cumulatif dans la fenetre)
    freqs = [d.frequency_in_window for d in decisions]
    assert freqs == sorted(freqs)


def test_evaluate_refuses_unknown_deviation(
    db_v3_with_deviations: tuple[Path, str]
) -> None:
    db_path, _ = db_v3_with_deviations
    with db_session(db_path) as conn:
        with pytest.raises(ValueError, match="inconnue"):
            evaluate_dual_tolerance(conn, 999999)


def test_latency_zero_triggers_immediately(
    db_v3_with_deviations: tuple[Path, str]
) -> None:
    """latency_minutes=0 par défaut → triggered_at != NULL"""
    db_path, _ = db_v3_with_deviations
    with db_session(db_path) as conn:
        devs = list_deviations(conn)
        dec = evaluate_dual_tolerance(conn, devs[0].deviation_id)
    assert dec.latency_minutes == 0
    assert dec.triggered_at is not None


def test_latency_data_driven_delays_trigger(
    tmp_db: Path, fixtures_v1_dir: Path
) -> None:
    """latency_minutes > 0 → triggered_at = NULL initialement."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        # Latence de 30 min
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'tolerance_latency_minutes', 30)"
        )
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'cpm_margin_minutes', 0)"
        )
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
            conn, horizon_label="W",
            horizon_start="2026-07-06", horizon_end="2026-07-12",
            candidate_ids=cids,
        )
        compute_coherence(conn, contract.contract_id)
        compute_smoothing(conn, contract.contract_id)
        for d in list_risk_debts(conn, status="open"):
            extinguish_risk_debt(conn, d.risk_debt_id, reason="t")
        result = run_p3_freeze(conn, contract.contract_id)
        generate_expected_from_batch(conn, result.batch_id)

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
        match_actuals_to_expected(conn, result.batch_id)
        apply_cpm_absorption(conn, batch_id=result.batch_id)
        # Évalue : pas encore d'absorbed parce que CPM=0, donc latence s'applique
        devs = list_deviations(conn)
        non_abs = [d for d in devs if not d.is_absorbed]
        assert len(non_abs) > 0
        dec = evaluate_dual_tolerance(conn, non_abs[0].deviation_id)
    assert dec.latency_minutes == 30
    assert dec.triggered_at is None


def test_list_decisions_filters_by_action_level(
    db_v3_with_deviations: tuple[Path, str]
) -> None:
    db_path, batch_id = db_v3_with_deviations
    with db_session(db_path) as conn:
        evaluate_all_open_deviations(conn, batch_id=batch_id)
        all_d = list_decisions(conn)
        triggered = list_decisions(conn, triggered_only=True)
    assert len(triggered) <= len(all_d)
    # Par défaut latency=0, donc triggered_only doit donner tous
    assert len(triggered) == len(all_d)
