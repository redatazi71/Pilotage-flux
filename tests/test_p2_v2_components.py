"""Tests de l'evaluateur R-P2-05 enrichi V2 (vraie evaluation stocks+achats)."""

from pathlib import Path

import pytest

from pilotage_flux.aps import compute_candidates
from pilotage_flux.db import db_session
from pilotage_flux.gates import (
    DECISION_BLOCK,
    DECISION_PASS,
    DECISION_PASS_WITH_RISK,
    evaluate_p2_for_candidate,
)
from pilotage_flux.importers import import_referentials
from pilotage_flux.stocks_purchasing import create_purchase, set_stock


@pytest.fixture
def db_with_candidates(tmp_db: Path, fixtures_v1_dir: Path) -> Path:
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        compute_candidates(conn)
    return tmp_db


def _first_ART_A_candidate_id(db_path: Path) -> str:
    with db_session(db_path) as conn:
        return conn.execute(
            "SELECT candidate_id FROM candidate_orders "
            "WHERE article_id = 'ART-A' AND quantity = 100"
        ).fetchone()["candidate_id"]


def test_p2_v2_pass_when_stock_covers_full(db_with_candidates: Path) -> None:
    """Stock suffisant pour tous composants -> R-P2-05 = PASS, decision P2 = PASS."""
    cid = _first_ART_A_candidate_id(db_with_candidates)
    # ART-A 100 -> SEMI-1 100 -> COMP-X 200 ; ART-A -> COMP-Y 100
    with db_session(db_with_candidates) as conn:
        set_stock(conn, "COMP-X", 500)
        set_stock(conn, "COMP-Y", 500)
        result = evaluate_p2_for_candidate(conn, cid)
    r5 = next(r for r in result.rule_results if r.rule_id == "R-P2-05")
    assert r5.outcome == "PASS"
    assert result.decision == DECISION_PASS


def test_p2_v2_block_when_coverage_below_50(db_with_candidates: Path) -> None:
    """Si la couverture totale < 50%, R-P2-05 doit BLOCK."""
    cid = _first_ART_A_candidate_id(db_with_candidates)
    with db_session(db_with_candidates) as conn:
        # Besoin : 200 COMP-X + 100 COMP-Y = 300. Stock 100 + 30 = 130 (43%)
        set_stock(conn, "COMP-X", 100)
        set_stock(conn, "COMP-Y", 30)
        result = evaluate_p2_for_candidate(conn, cid)
    r5 = next(r for r in result.rule_results if r.rule_id == "R-P2-05")
    assert r5.outcome == "BLOCK"
    assert result.decision == DECISION_BLOCK


def test_p2_v2_risk_when_partial_coverage(db_with_candidates: Path) -> None:
    """Couverture > 50% mais < 100% -> RISK + risk_debt cree."""
    cid = _first_ART_A_candidate_id(db_with_candidates)
    with db_session(db_with_candidates) as conn:
        # Besoin 200 + 100 = 300. Stock 150 + 80 = 230 (77%)
        set_stock(conn, "COMP-X", 150)
        set_stock(conn, "COMP-Y", 80)
        result = evaluate_p2_for_candidate(conn, cid)
    r5 = next(r for r in result.rule_results if r.rule_id == "R-P2-05")
    assert r5.outcome == "RISK"
    assert result.decision == DECISION_PASS_WITH_RISK
    assert len(result.risk_debts) >= 1


def test_p2_v2_open_po_compensates_stock_shortage(db_with_candidates: Path) -> None:
    """Stock faible mais PO ouvert compense -> PASS."""
    cid = _first_ART_A_candidate_id(db_with_candidates)
    with db_session(db_with_candidates) as conn:
        # Stock 50 COMP-X + 30 COMP-Y, PO 200 COMP-X + 80 COMP-Y
        # Projection : 250 COMP-X (need 200) + 110 COMP-Y (need 100) => PASS
        set_stock(conn, "COMP-X", 50)
        set_stock(conn, "COMP-Y", 30)
        create_purchase(conn, article_id="COMP-X", qty_ordered=200)
        create_purchase(conn, article_id="COMP-Y", qty_ordered=80)
        result = evaluate_p2_for_candidate(conn, cid)
    r5 = next(r for r in result.rule_results if r.rule_id == "R-P2-05")
    assert r5.outcome == "PASS"


def test_p2_v2_retro_compat_when_no_stock_data(db_with_candidates: Path) -> None:
    """Sans aucun stock/PO importe, on retombe sur le comportement V1.3 (RISK)."""
    cid = _first_ART_A_candidate_id(db_with_candidates)
    with db_session(db_with_candidates) as conn:
        result = evaluate_p2_for_candidate(conn, cid)
    r5 = next(r for r in result.rule_results if r.rule_id == "R-P2-05")
    assert r5.outcome == "RISK"
    assert "V1.3" in r5.explanation
