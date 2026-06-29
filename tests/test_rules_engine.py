"""Tests du moteur de regles."""

from pathlib import Path

import pytest

from pilotage_flux.aps import compute_candidates
from pilotage_flux.db import db_session
from pilotage_flux.importers import import_referentials
from pilotage_flux.rules import (
    OUTCOME_BLOCK,
    OUTCOME_PASS,
    RuleContext,
    RuleResult,
    evaluate_gate,
    evaluate_rule,
    load_active_rules,
)
from pilotage_flux.rules.engine import _register


@pytest.fixture
def db_v1(tmp_db: Path, fixtures_v1_dir: Path) -> Path:
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        compute_candidates(conn)
    return tmp_db


def test_load_active_rules_returns_5_p2_rules(db_v1: Path) -> None:
    """Les 5 regles P2 seedees par schema.sql sont chargees."""
    with db_session(db_v1) as conn:
        rules = load_active_rules(conn, "P2")
    rule_ids = {r.rule_id for r in rules}
    assert rule_ids == {"R-P2-01", "R-P2-02", "R-P2-03", "R-P2-04", "R-P2-05"}


def test_load_active_rules_returns_only_latest_version(tmp_db: Path) -> None:
    """Si une regle a deux versions actives, on prend la plus recente."""
    with db_session(tmp_db) as conn:
        conn.execute(
            """
            INSERT INTO decision_rules
                (rule_id, gate, criterion, label, version)
            VALUES ('R-TEST', 'P2', 'referentials_present', 'v2', 2)
            """
        )
        rules = load_active_rules(conn, "P2")
        test_rule = [r for r in rules if r.rule_id == "R-TEST"]
    assert len(test_rule) == 1
    assert test_rule[0].version == 2


def test_evaluate_rule_with_unknown_criterion_returns_block(db_v1: Path) -> None:
    """Une regle pointant sur un evaluateur non enregistre renvoie BLOCK."""
    with db_session(db_v1) as conn:
        conn.execute(
            """
            INSERT INTO decision_rules (rule_id, gate, criterion, label)
            VALUES ('R-UNK', 'P2', 'unknown_criterion_xyz', 'Test inconnu')
            """
        )
        rules = load_active_rules(conn, "P2")
        unknown = next(r for r in rules if r.rule_id == "R-UNK")
        cid = conn.execute(
            "SELECT candidate_id FROM candidate_orders LIMIT 1"
        ).fetchone()["candidate_id"]
        ctx = RuleContext(conn=conn, candidate_id=cid)
        result = evaluate_rule(ctx, unknown)
    assert result.outcome == OUTCOME_BLOCK
    assert "non enregistre" in result.explanation


def test_evaluate_rule_with_failing_evaluator_returns_block(db_v1: Path) -> None:
    """Un evaluateur qui leve une exception est converti en BLOCK propre."""
    def _crash(ctx):
        raise RuntimeError("boom")

    _register("crash_eval", _crash)
    with db_session(db_v1) as conn:
        conn.execute(
            """
            INSERT INTO decision_rules (rule_id, gate, criterion, label)
            VALUES ('R-CRASH', 'P2', 'crash_eval', 'Crash test')
            """
        )
        rules = load_active_rules(conn, "P2")
        rule = next(r for r in rules if r.rule_id == "R-CRASH")
        cid = conn.execute(
            "SELECT candidate_id FROM candidate_orders LIMIT 1"
        ).fetchone()["candidate_id"]
        ctx = RuleContext(conn=conn, candidate_id=cid)
        result = evaluate_rule(ctx, rule)
    assert result.outcome == OUTCOME_BLOCK
    assert "Erreur evaluateur" in result.explanation


def test_evaluate_gate_persists_results_in_gate_evaluations(db_v1: Path) -> None:
    """Chaque appel a evaluate_gate inscrit les 5 lignes en table."""
    with db_session(db_v1) as conn:
        cid = conn.execute(
            "SELECT candidate_id FROM candidate_orders LIMIT 1"
        ).fetchone()["candidate_id"]
        results = evaluate_gate(conn, cid, "P2")
        rows = conn.execute(
            "SELECT COUNT(*) AS n FROM gate_evaluations WHERE subject_id = ?",
            (cid,),
        ).fetchone()
    assert len(results) == 5
    assert int(rows["n"]) == 5
