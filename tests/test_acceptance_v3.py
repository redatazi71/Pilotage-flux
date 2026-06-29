"""Test d'acceptation V3 - pipeline événementiel lean bout-en-bout.

Étend V2 avec :
  - Génération expected_events depuis tranche gelée
  - Exécution MES → events réels
  - Matching attendu/réel → event_deviations
  - Absorption CPM (niveau 0)
  - Causes racines bayésiennes pondérées
  - Filtre dual de tolérances → décisions proportionnées
  - Filtre dual de mémoire P4 → apprentissage

Vérifie l'invariant doctrinal §7 bis.5 : le score détermine si la
recette devient réutilisable.
"""

from pathlib import Path

import pytest

from pilotage_flux.aps import compute_candidates
from pilotage_flux.db import db_session
from pilotage_flux.events_v3 import (
    ACTION_LEVELS,
    apply_cpm_absorption,
    attach_causes_to_deviation,
    capture_recipe,
    evaluate_all_open_deviations,
    generate_expected_from_batch,
    list_active_rules,
    list_decisions,
    list_deviations,
    list_memory_decisions,
    list_recipes,
    match_actuals_to_expected,
    top_causes_across_deviations,
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


def test_v3_full_event_loop_end_to_end(
    tmp_db: Path, fixtures_v1_dir: Path
) -> None:
    """Pipeline V3 : freeze → expected → exécution → match → CPM → causes →
    filtre dual tolérances → filtre dual mémoire."""

    # ============================================================
    # 1. PRÉ-REQUIS V0-V1-V2 : freeze d'un contrat avec stocks couvrants
    # ============================================================
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        # Configure paramètres V3 : CPM faible, seuils filtre dual data-driven
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'cpm_margin_minutes', 5)"
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
    batch_id = result.batch_id

    # ============================================================
    # 2. L3.1 - GÉNÉRATION ÉVÉNEMENTS ATTENDUS
    # ============================================================
    with db_session(tmp_db) as conn:
        expected = generate_expected_from_batch(conn, batch_id)
    # 4 candidates × (op_starts + op_finishes + of_close) = 16 events
    assert len(expected) == 16

    # ============================================================
    # 3. EXÉCUTION MES sur 2 OF (SEMI-1 et ART-A) pour générer events réels
    # ============================================================
    with db_session(tmp_db) as conn:
        for of_id in conn.execute(
            "SELECT of_id FROM manufacturing_orders "
            "WHERE article_id IN ('SEMI-1', 'ART-A') AND quantity IN (100, 50) "
            "LIMIT 2"
        ).fetchall():
            launch_of(conn, of_id["of_id"])
            ops = conn.execute(
                "SELECT of_op_id FROM order_operations "
                "WHERE of_id = ? ORDER BY sequence_idx",
                (of_id["of_id"],),
            ).fetchall()
            for op in ops:
                start_operation(conn, op["of_op_id"])
                finish_operation(
                    conn, op["of_op_id"], qty_good=95, qty_scrap=5
                )
            close_of(conn, of_id["of_id"])

    # ============================================================
    # 4. L3.2 - MATCHING ATTENDU/RÉEL
    # ============================================================
    with db_session(tmp_db) as conn:
        deviations = match_actuals_to_expected(conn, batch_id)
    # Au moins 6 déviations (2 OF × (start + finish + close))
    assert len(deviations) >= 6
    for d in deviations:
        assert d.deviation_kind == "time_delta"
        assert d.score is not None
        assert d.qualification in ("low", "medium", "high", "critical")

    # ============================================================
    # 5. L3.3 - ABSORPTION CPM (marge 5 min)
    # ============================================================
    with db_session(tmp_db) as conn:
        absorptions = apply_cpm_absorption(conn, batch_id=batch_id)
        absorbed_count = sum(1 for a in absorptions if a.is_absorbed)
        not_absorbed_count = sum(1 for a in absorptions if not a.is_absorbed)
    # Avec marge 5 min et écarts typiques en minutes/jours, la majorité ne sera pas absorbée
    assert not_absorbed_count > 0

    # ============================================================
    # 6. L3.4 - CAUSES RACINES BAYÉSIENNES
    # ============================================================
    with db_session(tmp_db) as conn:
        for d in list_deviations(conn):
            if not d.is_absorbed:
                attach_causes_to_deviation(conn, d.deviation_id)
        top = top_causes_across_deviations(conn, limit=5)
        rules = list_active_rules(conn)
    # 6 causes seedées disponibles
    assert len(rules) >= 6
    # Au moins une cause attachée
    assert len(top) > 0

    # ============================================================
    # 7. L3.5 - FILTRE DUAL DE TOLÉRANCES
    # ============================================================
    with db_session(tmp_db) as conn:
        tol_decisions = evaluate_all_open_deviations(conn, batch_id=batch_id)
    # Une décision par déviation
    assert len(tol_decisions) == len(deviations)
    # Chaque décision a un action_level valide
    for td in tol_decisions:
        assert td.action_level in ACTION_LEVELS

    # ============================================================
    # 8. L3.6 - FILTRE DUAL DE MÉMOIRE (apprentissage)
    # ============================================================
    with db_session(tmp_db) as conn:
        # Capture la recette pour les OFs clôturés
        closed_ofs = conn.execute(
            "SELECT of_id FROM manufacturing_orders WHERE status = 'closed'"
        ).fetchall()
        recipes = []
        for of_row in closed_ofs:
            recipe, decision = capture_recipe(
                conn, of_id=of_row["of_id"], outcome="success"
            )
            recipes.append((recipe, decision))
    assert len(recipes) >= 2
    # Chaque recette a une signature et un score
    for r, _ in recipes:
        assert r.deviation_signature
        assert r.score_combined is not None
        assert 0 <= r.score_combined <= 1

    # ============================================================
    # 9. VALIDATION DOCTRINE §7 bis.5 :
    #    Le score détermine si la recette est retenue pour apprentissage
    # ============================================================
    with db_session(tmp_db) as conn:
        # Force seuil bas → toutes recettes suivantes retenues
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'memory_learning_threshold', 0.05)"
        )
        # Re-capture sur le dernier OF pour observer le changement
        last_of_id = closed_ofs[-1]["of_id"]
        recipe2, decision2 = capture_recipe(conn, of_id=last_of_id, outcome="success")
    # Avec seuil bas, la décision passe à 'retain'
    assert decision2.decision == "retain"
    assert recipe2.is_retained is True

    # ============================================================
    # 10. APPRENTISSAGE : mise à jour d'un paramètre depuis recette retenue
    # ============================================================
    with db_session(tmp_db) as conn:
        decision = update_parameter_from_learning(
            conn, recipe2.recipe_id,
            parameter_name="cpm_margin_minutes",
            new_value=30,
            explanation="apprentissage : élargir la marge CPM après écarts récurrents",
        )
        # Vérifie que le paramètre est bien à 30 maintenant
        new_val = conn.execute(
            "SELECT value_num FROM parameters "
            "WHERE name = 'cpm_margin_minutes' AND valid_to IS NULL "
            "ORDER BY version DESC LIMIT 1"
        ).fetchone()["value_num"]
    assert decision.decision == "update_rule"
    assert decision.old_value == 5  # ancienne valeur fixture
    assert decision.new_value == 30
    assert new_val == 30

    # ============================================================
    # 11. TRAÇABILITÉ COMPLÈTE : audit memory_filter_decisions
    # ============================================================
    with db_session(tmp_db) as conn:
        all_memory_decisions = list_memory_decisions(conn)
        update_decisions = list_memory_decisions(conn, decision="update_rule")
    assert len(all_memory_decisions) >= len(recipes) + 1  # 1 par capture + 1 update
    assert len(update_decisions) == 1
    assert update_decisions[0].parameter_updated == "cpm_margin_minutes"
