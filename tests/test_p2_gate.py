"""Tests de la porte P2 (orchestration + 5 criteres + decision)."""

from datetime import date, timedelta
from pathlib import Path

import pytest

from pilotage_flux.aps import compute_candidates
from pilotage_flux.db import db_session
from pilotage_flux.gates import (
    DECISION_BLOCK,
    DECISION_PASS,
    DECISION_PASS_WITH_RISK,
    DECISION_RECALCULATE,
    evaluate_p2_for_candidate,
    run_p2_on_libre_zone,
)
from pilotage_flux.importers import import_referentials
from pilotage_flux.risk_debt import list_risk_debts
from pilotage_flux.zones import ZONE_LIBRE, ZONE_NEGOCIABLE, current_zone


@pytest.fixture
def db_v1(tmp_db: Path, fixtures_v1_dir: Path) -> Path:
    """Base V1 avec candidates en zone libre."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        compute_candidates(conn)
    return tmp_db


def test_p2_full_pass_transitions_to_negociable(db_v1: Path) -> None:
    """Un candidate SEMI-1 (charge faible, sans composant achete) passe en PASS."""
    with db_session(db_v1) as conn:
        # SEMI-1 a 1 op sur WS-1, charge faible
        cid = conn.execute(
            "SELECT candidate_id FROM candidate_orders "
            "WHERE article_id = 'SEMI-1' AND quantity = 50 "
            "LIMIT 1"
        ).fetchone()["candidate_id"]
        result = evaluate_p2_for_candidate(conn, cid)
        zone = current_zone(conn, cid)
    # SEMI-1 a COMP-X comme composant achete -> R-P2-05 retourne RISK, decision PASS_WITH_RISK
    # (V1.3 : pas de modelisation stocks/achats encore)
    assert result.decision in (DECISION_PASS, DECISION_PASS_WITH_RISK)
    assert result.transitioned is True
    assert zone == ZONE_NEGOCIABLE


def test_p2_overloaded_candidate_creates_risk_debt(db_v1: Path) -> None:
    """ART-A 100 sur WS-2 (capacity_factor 0.80) doit creer une risk_debt
    pour le critere bottleneck_capacity, en plus de celui composants_projetables."""
    with db_session(db_v1) as conn:
        cid = conn.execute(
            "SELECT candidate_id FROM candidate_orders "
            "WHERE article_id = 'ART-A' AND quantity = 100"
        ).fetchone()["candidate_id"]
        result = evaluate_p2_for_candidate(conn, cid)
        debts = list_risk_debts(conn, candidate_id=cid)
    # WS-2 : 100 * 3.0 = 300 min charge, capacite 480 * 0.80 = 384 min ratio 0.78
    # WS-3 : 100 * 1.2 = 120 min, capacite 480 * 0.95 = 456 min ratio 0.26
    # -> bottleneck_capacity = PASS (charge < seuil_risk)
    # MAIS components_projectable = RISK (V1.3) car composants COMP-X, COMP-Y
    assert result.decision == DECISION_PASS_WITH_RISK
    criteria_with_debt = {d.criterion for d in debts}
    assert "components_projectable" in criteria_with_debt


def test_p2_block_when_referentials_missing(db_v1: Path) -> None:
    """Un candidate dont l'article disparait du referentiel doit etre BLOCK."""
    with db_session(db_v1) as conn:
        # Cree un candidate orphelin
        conn.execute(
            "INSERT INTO candidate_orders "
            "(candidate_id, sales_order_id, article_id, quantity, status) "
            "VALUES ('CND-ORPHAN', 'SO-001', 'ART-A', 10, 'candidate')"
        )
        # Supprime sa gamme en cassant volontairement la coherence
        conn.execute("DELETE FROM routing_operations WHERE article_id = 'ART-A'")
        result = evaluate_p2_for_candidate(conn, "CND-ORPHAN")
        zone = current_zone(conn, "CND-ORPHAN")
    assert result.decision == DECISION_BLOCK
    assert result.transitioned is False
    assert zone == ZONE_LIBRE


def test_p2_recalculate_when_due_date_passed(tmp_db: Path, fixtures_v1_dir: Path) -> None:
    """Un SO avec due_date passee provoque RECALCULATE (et non BLOCK ni PASS)."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        # Force la due_date du SO-001 dans le passe
        past = (date.today() - timedelta(days=5)).isoformat()
        conn.execute(
            "UPDATE sales_orders SET due_date = ? WHERE sales_order_id = 'SO-001'",
            (past,),
        )
        compute_candidates(conn)
        cid = conn.execute(
            "SELECT candidate_id FROM candidate_orders "
            "WHERE sales_order_id = 'SO-001' AND article_id = 'ART-A'"
        ).fetchone()["candidate_id"]
        result = evaluate_p2_for_candidate(conn, cid)
        zone = current_zone(conn, cid)
    assert result.decision == DECISION_RECALCULATE
    assert result.transitioned is False
    assert zone == ZONE_LIBRE


def test_p2_batch_processes_all_libre_candidates(db_v1: Path) -> None:
    """run_p2_on_libre_zone traite tous les candidates libres en une fois."""
    with db_session(db_v1) as conn:
        n_libre_before = conn.execute(
            "SELECT COUNT(*) AS n FROM candidate_orders WHERE zone = 'libre'"
        ).fetchone()["n"]
        batch = run_p2_on_libre_zone(conn)
        n_libre_after = conn.execute(
            "SELECT COUNT(*) AS n FROM candidate_orders WHERE zone = 'libre'"
        ).fetchone()["n"]
        n_negociable = conn.execute(
            "SELECT COUNT(*) AS n FROM candidate_orders WHERE zone = 'negociable'"
        ).fetchone()["n"]
    assert n_libre_before == 4
    assert len(batch.results) == 4
    # Avec les fixtures V1 + V1.3 : tous passent (PASS_WITH_RISK car
    # components_projectable est en RISK par defaut)
    assert batch.passed_with_risk == 4
    assert batch.passed == 0
    assert batch.blocked == 0
    assert n_libre_after == 0
    assert n_negociable == 4
    assert batch.total_risk_debts == 4


def test_p2_decision_is_recorded_in_gate_decisions_v1(db_v1: Path) -> None:
    with db_session(db_v1) as conn:
        cid = conn.execute(
            "SELECT candidate_id FROM candidate_orders LIMIT 1"
        ).fetchone()["candidate_id"]
        evaluate_p2_for_candidate(conn, cid)
        row = conn.execute(
            "SELECT decision, risk_count FROM gate_decisions_v1 "
            "WHERE subject_id = ? AND gate = 'P2'",
            (cid,),
        ).fetchone()
    assert row is not None
    assert row["decision"] in (DECISION_PASS, DECISION_PASS_WITH_RISK, DECISION_RECALCULATE, DECISION_BLOCK)


def test_p2_is_data_driven_risk_threshold(tmp_db: Path, fixtures_v1_dir: Path) -> None:
    """Modifier p2_capacity_risk_ratio en base change le comportement de P2."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        compute_candidates(conn)
        # Force un seuil RISK tres bas pour declencher RISK sur charge legere
        conn.execute(
            "UPDATE parameters SET value_num = 0.05 "
            "WHERE scope = 'global' AND name = 'p2_capacity_risk_ratio'"
        )
        # SEMI-1 50 unites: WS-1 100 min, capa 480*0.85=408 -> ratio 0.24 > 0.05 = RISK
        cid = conn.execute(
            "SELECT candidate_id FROM candidate_orders "
            "WHERE article_id = 'SEMI-1' AND quantity = 50"
        ).fetchone()["candidate_id"]
        result = evaluate_p2_for_candidate(conn, cid)
    capacity_results = [
        r for r in result.rule_results if r.criterion == "bottleneck_capacity"
    ]
    assert len(capacity_results) == 1
    assert capacity_results[0].outcome == "RISK"
