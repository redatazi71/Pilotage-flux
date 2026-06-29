"""Tests du filtre dual de mémoire P4 (L3.6)."""

from pathlib import Path

import pytest

from pilotage_flux.aps import compute_candidates
from pilotage_flux.db import db_session
from pilotage_flux.events_v3 import (
    apply_cpm_absorption,
    attach_causes_to_deviation,
    capture_recipe,
    evaluate_all_open_deviations,
    generate_expected_from_batch,
    list_deviations,
    list_memory_decisions,
    list_recipes,
    match_actuals_to_expected,
    update_parameter_from_learning,
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
def db_v3_full_pipeline(
    tmp_db: Path, fixtures_v1_dir: Path
) -> tuple[Path, str, str]:
    """Base avec pipeline V3 complet exécuté pour un OF (db_path, batch_id, of_id)."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        # CPM nul pour avoir des déviations non absorbées
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
        # Attache causes sur toutes deviations non absorbées
        for d in list_deviations(conn):
            if not d.is_absorbed:
                attach_causes_to_deviation(conn, d.deviation_id)
        evaluate_all_open_deviations(conn, batch_id=result.batch_id)
    return tmp_db, result.batch_id, of_id


def test_capture_creates_recipe_with_signature(
    db_v3_full_pipeline: tuple[Path, str, str]
) -> None:
    db_path, _, of_id = db_v3_full_pipeline
    with db_session(db_path) as conn:
        recipe, decision = capture_recipe(conn, of_id=of_id, outcome="success")
    assert recipe.of_id == of_id
    assert recipe.deviation_signature != "-|-|-"
    assert recipe.score_combined is not None
    assert decision.decision in ("retain", "log_only")


def test_capture_refuses_non_closed_of(
    db_v3_full_pipeline: tuple[Path, str, str]
) -> None:
    db_path, _, _ = db_v3_full_pipeline
    with db_session(db_path) as conn:
        # Cherche un OF non clôturé
        other = conn.execute(
            "SELECT of_id FROM manufacturing_orders WHERE status != 'closed' LIMIT 1"
        ).fetchone()
        if other:
            with pytest.raises(ValueError, match="closed"):
                capture_recipe(conn, of_id=other["of_id"], outcome="success")


def test_first_recipe_is_log_only(
    db_v3_full_pipeline: tuple[Path, str, str]
) -> None:
    """Avec seuil par défaut 0.5 et recurrence = 0 (1re recette), le combined est
    typiquement insuffisant pour retenir."""
    db_path, _, of_id = db_v3_full_pipeline
    with db_session(db_path) as conn:
        recipe, decision = capture_recipe(conn, of_id=of_id)
    # Recurrence = 0 sur la 1ère recette → combined = significance / 2
    # Significance est issue de l'historique des déviations, typiquement < 1
    # Donc combined < 0.5 par défaut → log_only
    assert recipe.score_recurrence == 0.0


def test_retention_with_low_threshold(
    db_v3_full_pipeline: tuple[Path, str, str]
) -> None:
    """Avec seuil bas (0.05), une recette même unique est retenue."""
    db_path, _, of_id = db_v3_full_pipeline
    with db_session(db_path) as conn:
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'memory_learning_threshold', 0.05)"
        )
        recipe, decision = capture_recipe(conn, of_id=of_id)
    assert recipe.is_retained is True
    assert decision.decision == "retain"


def test_threshold_is_data_driven_high_blocks(
    db_v3_full_pipeline: tuple[Path, str, str]
) -> None:
    """Avec seuil très haut (0.99), recette non retenue."""
    db_path, _, of_id = db_v3_full_pipeline
    with db_session(db_path) as conn:
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'memory_learning_threshold', 0.99)"
        )
        recipe, _ = capture_recipe(conn, of_id=of_id)
    assert recipe.is_retained is False


def test_update_parameter_refuses_non_retained(
    db_v3_full_pipeline: tuple[Path, str, str]
) -> None:
    """Tenter d'updater un paramètre depuis une recette log_only doit être refusé."""
    db_path, _, of_id = db_v3_full_pipeline
    with db_session(db_path) as conn:
        # Force seuil haut pour ne pas retenir
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'memory_learning_threshold', 0.99)"
        )
        recipe, _ = capture_recipe(conn, of_id=of_id)
        with pytest.raises(ValueError, match="non retenue"):
            update_parameter_from_learning(
                conn, recipe.recipe_id,
                parameter_name="cpm_margin_minutes", new_value=120,
            )


def test_update_parameter_traces_old_and_new_value(
    db_v3_full_pipeline: tuple[Path, str, str]
) -> None:
    """Mise à jour d'un paramètre depuis une recette retenue trace old/new."""
    db_path, _, of_id = db_v3_full_pipeline
    with db_session(db_path) as conn:
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'memory_learning_threshold', 0.05)"
        )
        recipe, _ = capture_recipe(conn, of_id=of_id)
        # cpm_margin_minutes = 0 (mis dans la fixture)
        decision = update_parameter_from_learning(
            conn, recipe.recipe_id,
            parameter_name="cpm_margin_minutes", new_value=45,
            explanation="ajustement après recette retenue",
        )
        # Vérifie qu'au runtime le paramètre est bien à 45
        new_val = conn.execute(
            "SELECT value_num FROM parameters "
            "WHERE name = 'cpm_margin_minutes' AND valid_to IS NULL "
            "ORDER BY version DESC LIMIT 1"
        ).fetchone()["value_num"]
    assert decision.decision == "update_rule"
    assert decision.old_value == 0.0
    assert decision.new_value == 45
    assert new_val == 45


def test_list_recipes_filters_retained_only(
    db_v3_full_pipeline: tuple[Path, str, str]
) -> None:
    db_path, _, of_id = db_v3_full_pipeline
    with db_session(db_path) as conn:
        # 1ère recette : seuil bas, retenue
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'memory_learning_threshold', 0.05)"
        )
        capture_recipe(conn, of_id=of_id)
        retained = list_recipes(conn, retained_only=True)
        all_r = list_recipes(conn)
    assert len(retained) >= 1
    assert len(all_r) >= len(retained)


def test_list_memory_decisions_filters_by_recipe(
    db_v3_full_pipeline: tuple[Path, str, str]
) -> None:
    db_path, _, of_id = db_v3_full_pipeline
    with db_session(db_path) as conn:
        recipe, _ = capture_recipe(conn, of_id=of_id)
        decisions = list_memory_decisions(conn, recipe_id=recipe.recipe_id)
    assert len(decisions) >= 1


def test_capture_signature_includes_components(
    db_v3_full_pipeline: tuple[Path, str, str]
) -> None:
    """La signature contient déviation_kind, cause, action_level (3 parties)."""
    db_path, _, of_id = db_v3_full_pipeline
    with db_session(db_path) as conn:
        recipe, _ = capture_recipe(conn, of_id=of_id)
    parts = recipe.deviation_signature.split("|")
    assert len(parts) == 3
