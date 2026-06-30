"""Moteur Delta actif — V12.3 architecture cybernétique étendue.

Étend le filtre dual de tolérance V3 en ajoutant une **matrice 4
niveaux d'autonomie** avec validation humaine pour les niveaux
critiques.

Composants exposés :

  - AutonomyLevel : enum L1..L4
  - classify_autonomy_level(action_level) : mapping V3 → V12.3
  - submit_to_approval_queue() : enqueue L3/L4 pour validation
  - approve_decision() / reject_decision() : actions humaines
  - auto_approve_with_lag() : simulation avec lag réaliste
"""

from pilotage_flux.cybernetic.delta_engine.autonomy_levels import (
    AUTONOMY_LEVEL_L1,
    AUTONOMY_LEVEL_L2,
    AUTONOMY_LEVEL_L3,
    AUTONOMY_LEVEL_L4,
    AUTONOMY_LEVELS,
    REQUIRES_APPROVAL,
    classify_autonomy_level,
    describe_level,
)
from pilotage_flux.cybernetic.delta_engine.approval_queue import (
    approve_decision,
    auto_approve_with_lag,
    list_pending,
    reject_decision,
    submit_to_approval_queue,
)
from pilotage_flux.cybernetic.delta_engine.dispatcher import (
    dispatch_decision,
)

__all__ = [
    "AUTONOMY_LEVEL_L1",
    "AUTONOMY_LEVEL_L2",
    "AUTONOMY_LEVEL_L3",
    "AUTONOMY_LEVEL_L4",
    "AUTONOMY_LEVELS",
    "REQUIRES_APPROVAL",
    "classify_autonomy_level",
    "describe_level",
    "submit_to_approval_queue",
    "approve_decision",
    "reject_decision",
    "list_pending",
    "auto_approve_with_lag",
    "dispatch_decision",
]
