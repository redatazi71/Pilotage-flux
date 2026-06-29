"""Porte P2 (libre -> negociable) - cadrage §6 / §17.

Evalue les 5 criteres standard P2 (référentiels, cohérence interne, validite
previsionnelle, charge goulot, composants projetables) puis prend une decision :

  PASS             : tous les criteres en PASS -> transition libre -> negociable
  PASS_WITH_RISK   : un ou plusieurs criteres en RISK -> meme transition,
                     mais creation d'une risk_debt par critere a risque
  RECALCULATE      : au moins un critere en RECALCULATE (et aucun BLOCK)
                     -> reste en libre, le candidate sera re-evalue plus tard
  BLOCK            : au moins un critere en BLOCK -> reste en libre, marque
                     en gate_decisions comme bloque, intervention manuelle requise
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from pilotage_flux.risk_debt import open_risk_debt, RiskDebt
from pilotage_flux.rules import (
    OUTCOME_BLOCK,
    OUTCOME_PASS,
    OUTCOME_RECALCULATE,
    OUTCOME_RISK,
    RuleResult,
    evaluate_gate,
)
from pilotage_flux.zones import (
    ZONE_LIBRE,
    ZONE_NEGOCIABLE,
    fetch_in_zone,
    move_candidate_to_zone,
)


DECISION_PASS = "PASS"
DECISION_PASS_WITH_RISK = "PASS_WITH_RISK"
DECISION_RECALCULATE = "RECALCULATE"
DECISION_BLOCK = "BLOCK"


@dataclass
class P2Result:
    candidate_id: str
    decision: str
    rule_results: list[RuleResult]
    risk_debts: list[RiskDebt] = field(default_factory=list)
    transitioned: bool = False


def _decide(rule_results: list[RuleResult]) -> str:
    """Aggregation des outcomes en decision P2 finale.

    Precedence : BLOCK > RECALCULATE > RISK > PASS.
    """
    outcomes = {r.outcome for r in rule_results}
    if OUTCOME_BLOCK in outcomes:
        return DECISION_BLOCK
    if OUTCOME_RECALCULATE in outcomes:
        return DECISION_RECALCULATE
    if OUTCOME_RISK in outcomes:
        return DECISION_PASS_WITH_RISK
    return DECISION_PASS


def evaluate_p2_for_candidate(
    conn: sqlite3.Connection,
    candidate_id: str,
    *,
    cycle_id: str | None = None,
    actor: str = "gate.p2",
) -> P2Result:
    """Evalue P2 sur un seul candidate. Conn doit deja etre ouverte."""
    rule_results = evaluate_gate(conn, candidate_id, "P2")
    decision = _decide(rule_results)

    risk_debts: list[RiskDebt] = []
    transitioned = False

    if decision in (DECISION_PASS, DECISION_PASS_WITH_RISK):
        if decision == DECISION_PASS_WITH_RISK:
            for r in rule_results:
                if r.outcome == OUTCOME_RISK:
                    debt = open_risk_debt(
                        conn,
                        candidate_id=candidate_id,
                        criterion=r.criterion,
                        rule_id=r.rule_id,
                        score=float(r.score) if r.score is not None else 0.5,
                        explanation=r.explanation,
                    )
                    risk_debts.append(debt)

        move_candidate_to_zone(
            conn,
            candidate_id,
            ZONE_NEGOCIABLE,
            decision=decision,
            rule_ref="gate.p2",
            explanation="; ".join(
                f"{r.rule_id}={r.outcome}" for r in rule_results
            ),
            cycle_id=cycle_id,
            actor=actor,
        )
        transitioned = True

    explanation = "; ".join(
        f"{r.rule_id}={r.outcome}" for r in rule_results
    )
    conn.execute(
        """
        INSERT INTO gate_decisions_v1
            (gate, subject_type, subject_id, cycle_id, decision,
             risk_count, explanation)
        VALUES ('P2', 'candidate_order', ?, ?, ?, ?, ?)
        """,
        (candidate_id, cycle_id, decision, len(risk_debts), explanation),
    )

    return P2Result(
        candidate_id=candidate_id,
        decision=decision,
        rule_results=rule_results,
        risk_debts=risk_debts,
        transitioned=transitioned,
    )


@dataclass
class P2BatchResult:
    cycle_id: str | None
    results: list[P2Result] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.decision == DECISION_PASS)

    @property
    def passed_with_risk(self) -> int:
        return sum(1 for r in self.results if r.decision == DECISION_PASS_WITH_RISK)

    @property
    def recalc(self) -> int:
        return sum(1 for r in self.results if r.decision == DECISION_RECALCULATE)

    @property
    def blocked(self) -> int:
        return sum(1 for r in self.results if r.decision == DECISION_BLOCK)

    @property
    def total_risk_debts(self) -> int:
        return sum(len(r.risk_debts) for r in self.results)


def run_p2_on_libre_zone(
    conn: sqlite3.Connection,
    *,
    cycle_id: str | None = None,
    actor: str = "gate.p2",
) -> P2BatchResult:
    """Execute P2 sur tous les candidates actuellement en zone 'libre'."""
    candidates = fetch_in_zone(conn, ZONE_LIBRE)
    batch = P2BatchResult(cycle_id=cycle_id)

    conn.execute("BEGIN")
    try:
        for cand in candidates:
            result = evaluate_p2_for_candidate(
                conn,
                cand["candidate_id"],
                cycle_id=cycle_id,
                actor=actor,
            )
            batch.results.append(result)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return batch
