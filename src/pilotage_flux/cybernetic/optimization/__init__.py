"""V12.2 — Optimisation zone négociable (CP-SAT dynamique + fallbacks).

Cette couche complète V12.1 (forecasting zone libre) en ajoutant le
solveur opérationnel de la zone négociable : entre la freeze window
(intangible) et l'horizon de forecast (incertain). C'est la zone où
le replanification est utile et possible.

Composants :

  - zone_resolver  : identifie les OFs/candidats dans la zone négociable
  - cp_sat_dynamic : CP-SAT re-plan focalisé sur cette zone uniquement
  - heuristics     : SLACK, EDD, SPT, ATC pour fallback rapide
  - ensemble       : sélection contextuelle (qualité vs vitesse)

Workflow V12 complet :

  V12.1 (forecast zone libre) → V12.2 (optim zone négociable)
    → V12.3 (Delta engine 4 niveaux, queue L3/L4)
    → V12.4 (validation humaine)
    → application au plan
"""

from pilotage_flux.cybernetic.optimization.cp_sat_dynamic import (
    ProposedReplan,
    propose_dynamic_replan,
)
from pilotage_flux.cybernetic.optimization.heuristics import (
    HEURISTIC_ATC,
    HEURISTIC_EDD,
    HEURISTIC_SLACK,
    HEURISTIC_SPT,
    schedule_heuristic,
)
from pilotage_flux.cybernetic.optimization.zone_resolver import (
    NegotiableZone,
    resolve_negotiable_zone,
)

__all__ = [
    "NegotiableZone",
    "resolve_negotiable_zone",
    "ProposedReplan",
    "propose_dynamic_replan",
    "HEURISTIC_SLACK",
    "HEURISTIC_EDD",
    "HEURISTIC_SPT",
    "HEURISTIC_ATC",
    "schedule_heuristic",
]
