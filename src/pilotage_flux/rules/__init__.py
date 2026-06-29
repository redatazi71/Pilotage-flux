"""Moteur de regles V1 (data-driven, sobre).

Les regles sont declarees en table `decision_rules` et referencent un
`criterion` qui pointe sur un evaluateur Python dans EVALUATORS. Les seuils
sont dans `parameters`. Pas de DSL plein en V1 — l'expression JSON est
descriptive ; le DSL viendra en V3 (filtre dual).
"""

from pilotage_flux.rules.engine import (
    EVALUATORS,
    OUTCOME_BLOCK,
    OUTCOME_PASS,
    OUTCOME_RECALCULATE,
    OUTCOME_RISK,
    Rule,
    RuleContext,
    RuleResult,
    evaluate_gate,
    evaluate_rule,
    load_active_rules,
)
from pilotage_flux.rules.evaluators import (
    eval_bottleneck_capacity,
    eval_components_projectable,
    eval_forecast_validity,
    eval_internal_coherence,
    eval_referentials_present,
)

__all__ = [
    "EVALUATORS",
    "OUTCOME_BLOCK",
    "OUTCOME_PASS",
    "OUTCOME_RECALCULATE",
    "OUTCOME_RISK",
    "Rule",
    "RuleContext",
    "RuleResult",
    "evaluate_gate",
    "evaluate_rule",
    "load_active_rules",
    "eval_bottleneck_capacity",
    "eval_components_projectable",
    "eval_forecast_validity",
    "eval_internal_coherence",
    "eval_referentials_present",
]
