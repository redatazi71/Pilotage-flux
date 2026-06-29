"""Moteur de regles minimal V1.

Une regle est une ligne de la table `decision_rules`. Son champ `criterion`
identifie l'evaluateur Python qui sera appele. Chaque evaluateur recoit un
`RuleContext` (connexion + candidate_id) et renvoie un `RuleResult` portant
outcome ∈ {PASS, RISK, RECALCULATE, BLOCK}, score optionnel et explication.

Cette mecanique sera etendue en V3 avec un vrai DSL d'expression. En V1
elle reste delib volontairement simple : c'est le mariage entre regles
data-driven (table) et evaluateurs code (Python).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable


OUTCOME_PASS = "PASS"
OUTCOME_RISK = "RISK"
OUTCOME_RECALCULATE = "RECALCULATE"
OUTCOME_BLOCK = "BLOCK"

VALID_OUTCOMES = {OUTCOME_PASS, OUTCOME_RISK, OUTCOME_RECALCULATE, OUTCOME_BLOCK}


@dataclass(frozen=True)
class Rule:
    rule_id: str
    gate: str
    criterion: str
    label: str
    severity: str
    version: int


@dataclass(frozen=True)
class RuleContext:
    """Contexte d'evaluation. Donne acces a la DB et identifie le sujet."""
    conn: sqlite3.Connection
    candidate_id: str


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    rule_version: int
    criterion: str
    outcome: str
    score: float | None
    explanation: str


# Type d'un evaluateur : prend un contexte, renvoie un resultat partiel
# (sans les rule_id/version, completes par le moteur).
EvaluatorFn = Callable[[RuleContext], tuple[str, float | None, str]]


# Registre des evaluateurs. Rempli par evaluators.py via _register().
EVALUATORS: dict[str, EvaluatorFn] = {}


def _register(criterion: str, fn: EvaluatorFn) -> None:
    """Enregistre un evaluateur Python pour un nom de critere."""
    EVALUATORS[criterion] = fn


def load_active_rules(conn: sqlite3.Connection, gate: str) -> list[Rule]:
    """Charge les regles actives (valid_to IS NULL) pour une porte."""
    rows = conn.execute(
        """
        SELECT rule_id, gate, criterion, label, severity, version
        FROM decision_rules
        WHERE gate = ? AND valid_to IS NULL
        ORDER BY rule_id ASC, version DESC
        """,
        (gate,),
    ).fetchall()
    # Garde une seule version par rule_id (la plus recente)
    seen: set[str] = set()
    out: list[Rule] = []
    for r in rows:
        if r["rule_id"] in seen:
            continue
        seen.add(r["rule_id"])
        out.append(
            Rule(
                rule_id=r["rule_id"],
                gate=r["gate"],
                criterion=r["criterion"],
                label=r["label"],
                severity=r["severity"],
                version=int(r["version"]),
            )
        )
    return out


def evaluate_rule(ctx: RuleContext, rule: Rule) -> RuleResult:
    """Evalue une regle en appelant l'evaluateur lie a son `criterion`."""
    evaluator = EVALUATORS.get(rule.criterion)
    if evaluator is None:
        return RuleResult(
            rule_id=rule.rule_id,
            rule_version=rule.version,
            criterion=rule.criterion,
            outcome=OUTCOME_BLOCK,
            score=None,
            explanation=f"Evaluateur '{rule.criterion}' non enregistre",
        )
    try:
        outcome, score, explanation = evaluator(ctx)
    except Exception as exc:  # noqa: BLE001
        return RuleResult(
            rule_id=rule.rule_id,
            rule_version=rule.version,
            criterion=rule.criterion,
            outcome=OUTCOME_BLOCK,
            score=None,
            explanation=f"Erreur evaluateur : {exc}",
        )
    if outcome not in VALID_OUTCOMES:
        outcome = OUTCOME_BLOCK
        explanation = f"Outcome invalide retourne par evaluateur : {outcome!r}"
    return RuleResult(
        rule_id=rule.rule_id,
        rule_version=rule.version,
        criterion=rule.criterion,
        outcome=outcome,
        score=score,
        explanation=explanation,
    )


def evaluate_gate(
    conn: sqlite3.Connection, candidate_id: str, gate: str
) -> list[RuleResult]:
    """Evalue toutes les regles actives d'une porte sur un candidate.

    Les resultats sont *aussi* persistes en `gate_evaluations`.
    """
    rules = load_active_rules(conn, gate)
    ctx = RuleContext(conn=conn, candidate_id=candidate_id)
    results = [evaluate_rule(ctx, r) for r in rules]
    for r in results:
        conn.execute(
            """
            INSERT INTO gate_evaluations
                (gate, subject_type, subject_id, rule_id, rule_version,
                 criterion, outcome, score, explanation)
            VALUES (?, 'candidate_order', ?, ?, ?, ?, ?, ?, ?)
            """,
            (gate, candidate_id, r.rule_id, r.rule_version, r.criterion,
             r.outcome, r.score, r.explanation),
        )
    return results
