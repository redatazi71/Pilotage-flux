"""Tests des causes racines bayésiennes (L3.4)."""

from pathlib import Path

import pytest

from pilotage_flux.aps import compute_candidates
from pilotage_flux.db import db_session
from pilotage_flux.events_v3 import (
    apply_cpm_absorption,
    attach_causes_to_deviation,
    confirm_cause,
    generate_expected_from_batch,
    list_active_rules,
    list_causes_for_deviation,
    list_deviations,
    match_actuals_to_expected,
    top_causes_across_deviations,
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
def db_v3_with_open_deviations(
    tmp_db: Path, fixtures_v1_dir: Path
) -> tuple[Path, str, int]:
    """Base avec freeze + execution + déviations (avec marge CPM stricte pour ne rien absorber)."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        # Marge CPM nulle = aucun écart absorbé
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

        # Une déviation non absorbée pour tester l'attache de causes
        not_absorbed = list_deviations(conn)
        not_absorbed = [d for d in not_absorbed if not d.is_absorbed]
        assert len(not_absorbed) > 0, "Avec margin=0 il devrait y avoir des non-absorbés"
        dev_id = not_absorbed[0].deviation_id

    return tmp_db, result.batch_id, dev_id


def test_seed_rules_loaded(db_v3_with_open_deviations: tuple[Path, str, int]) -> None:
    db_path, _, _ = db_v3_with_open_deviations
    with db_session(db_path) as conn:
        rules = list_active_rules(conn)
    rule_ids = {r.rule_id for r in rules}
    assert {"R-RC-01", "R-RC-02", "R-RC-03",
            "R-RC-04", "R-RC-05", "R-RC-06"}.issubset(rule_ids)


def test_attach_causes_filters_by_kind(
    db_v3_with_open_deviations: tuple[Path, str, int]
) -> None:
    """Pour une déviation time_delta, seules les causes time_delta sont proposées."""
    db_path, _, dev_id = db_v3_with_open_deviations
    with db_session(db_path) as conn:
        attaches = attach_causes_to_deviation(conn, dev_id, only_kind=True)
    # Causes applicables à time_delta : R-RC-01, R-RC-04, R-RC-06 (3 causes)
    assert len(attaches) == 3
    rule_ids = {a.rule_id for a in attaches}
    assert rule_ids == {"R-RC-01", "R-RC-04", "R-RC-06"}


def test_attach_causes_all_when_only_kind_false(
    db_v3_with_open_deviations: tuple[Path, str, int]
) -> None:
    db_path, _, dev_id = db_v3_with_open_deviations
    with db_session(db_path) as conn:
        attaches = attach_causes_to_deviation(conn, dev_id, only_kind=False)
    assert len(attaches) == 6  # toutes les causes seed


def test_attach_is_idempotent(
    db_v3_with_open_deviations: tuple[Path, str, int]
) -> None:
    db_path, _, dev_id = db_v3_with_open_deviations
    with db_session(db_path) as conn:
        first = attach_causes_to_deviation(conn, dev_id)
        second = attach_causes_to_deviation(conn, dev_id)
    assert len(first) > 0
    assert len(second) == 0


def test_attach_refuses_unknown_deviation(
    db_v3_with_open_deviations: tuple[Path, str, int]
) -> None:
    db_path, _, _ = db_v3_with_open_deviations
    with db_session(db_path) as conn:
        with pytest.raises(ValueError, match="inconnue"):
            attach_causes_to_deviation(conn, 999999)


def test_attach_returns_empty_for_absorbed_deviation(
    tmp_db: Path, fixtures_v1_dir: Path
) -> None:
    """Une déviation absorbée CPM n'a pas de causes racines proposées."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        # Marge énorme → tout est absorbé
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'cpm_margin_minutes', 999999)"
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

        devs = list_deviations(conn)
        absorbed = [d for d in devs if d.is_absorbed]
        assert len(absorbed) > 0
        attaches = attach_causes_to_deviation(conn, absorbed[0].deviation_id)
    assert attaches == []


def test_confirm_cause_increases_rule_confidence(
    db_v3_with_open_deviations: tuple[Path, str, int]
) -> None:
    """Confirmer une cause augmente la confidence (apprentissage bayésien simple)."""
    db_path, _, dev_id = db_v3_with_open_deviations
    with db_session(db_path) as conn:
        attaches = attach_causes_to_deviation(conn, dev_id)
        first_attach = attaches[0]
        # Lit la confidence avant
        conf_before = conn.execute(
            "SELECT confidence FROM root_cause_rules WHERE rule_id = ?",
            (first_attach.rule_id,),
        ).fetchone()["confidence"]

        confirm_cause(conn, first_attach.attach_id, explanation="validé par opérateur")

        conf_after = conn.execute(
            "SELECT confidence FROM root_cause_rules WHERE rule_id = ?",
            (first_attach.rule_id,),
        ).fetchone()["confidence"]
        # Posterior aussi maj
        post_after = conn.execute(
            "SELECT posterior, confirmed FROM event_deviation_causes WHERE attach_id = ?",
            (first_attach.attach_id,),
        ).fetchone()
    assert conf_after > conf_before
    assert conf_after <= 1.0  # cap à 1
    assert bool(post_after["confirmed"]) is True


def test_confirm_cause_is_idempotent(
    db_v3_with_open_deviations: tuple[Path, str, int]
) -> None:
    db_path, _, dev_id = db_v3_with_open_deviations
    with db_session(db_path) as conn:
        attaches = attach_causes_to_deviation(conn, dev_id)
        attach = attaches[0]
        confirm_cause(conn, attach.attach_id, explanation="x")
        # 2e appel sur même attach ne re-update pas la rule confidence
        conf1 = conn.execute(
            "SELECT confidence FROM root_cause_rules WHERE rule_id = ?",
            (attach.rule_id,),
        ).fetchone()["confidence"]
        confirm_cause(conn, attach.attach_id, explanation="y")
        conf2 = conn.execute(
            "SELECT confidence FROM root_cause_rules WHERE rule_id = ?",
            (attach.rule_id,),
        ).fetchone()["confidence"]
    assert conf1 == conf2


def test_list_causes_returns_sorted_by_score(
    db_v3_with_open_deviations: tuple[Path, str, int]
) -> None:
    db_path, _, dev_id = db_v3_with_open_deviations
    with db_session(db_path) as conn:
        attach_causes_to_deviation(conn, dev_id)
        causes = list_causes_for_deviation(conn, dev_id)
    scores = [c.score for c in causes]
    assert scores == sorted(scores, reverse=True)


def test_top_causes_aggregates_across_deviations(
    db_v3_with_open_deviations: tuple[Path, str, int]
) -> None:
    db_path, batch_id, _ = db_v3_with_open_deviations
    with db_session(db_path) as conn:
        # Attache causes sur toutes les déviations non absorbées
        all_devs = list_deviations(conn)
        for d in all_devs:
            if not d.is_absorbed:
                attach_causes_to_deviation(conn, d.deviation_id)
        top = top_causes_across_deviations(conn, limit=3)
    assert len(top) <= 3
    if top:
        # Sortie triée par total_score desc
        scores = [c["total_score"] for c in top]
        assert scores == sorted(scores, reverse=True)
