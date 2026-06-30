"""V12.3 — Matrice 4 niveaux d'autonomie + classification.

  L1 — autonome sans ajustement (écart absorbé par tampon)
  L2 — ajustement sans humain (V3 actionnel correct_local)
  L3 — replanification locale + validation humaine
  L4 — replanification totale + validation humaine

Mapping vers les 6 niveaux d'action V3 (events_v3/dual_tolerance.py) :

  inform       → L1 (juste tracé, pas d'action)
  watch        → L1 (juste tracé, pas d'action)
  correct_local→ L2 (V3 applique correction locale auto)
  replan_local → L3 (CP-SAT propose, humain valide)
  escalate     → L4 (refonte zone négociable, humain valide)
  replan_global→ L4 (refonte plan global, supervisor valide)
"""

from __future__ import annotations

from typing import Final

from pilotage_flux.events_v3.dual_tolerance import (
    ACTION_CORRECT_LOCAL,
    ACTION_ESCALATE,
    ACTION_INFORM,
    ACTION_REPLAN_GLOBAL,
    ACTION_REPLAN_LOCAL,
    ACTION_WATCH,
)


AUTONOMY_LEVEL_L1: Final[str] = "L1_absorbed"
AUTONOMY_LEVEL_L2: Final[str] = "L2_auto_adjust"
AUTONOMY_LEVEL_L3: Final[str] = "L3_local_replan_approval"
AUTONOMY_LEVEL_L4: Final[str] = "L4_global_replan_approval"

AUTONOMY_LEVELS: Final[tuple[str, ...]] = (
    AUTONOMY_LEVEL_L1,
    AUTONOMY_LEVEL_L2,
    AUTONOMY_LEVEL_L3,
    AUTONOMY_LEVEL_L4,
)

REQUIRES_APPROVAL: Final[frozenset[str]] = frozenset({
    AUTONOMY_LEVEL_L3, AUTONOMY_LEVEL_L4,
})


_ACTION_TO_LEVEL = {
    ACTION_INFORM: AUTONOMY_LEVEL_L1,
    ACTION_WATCH: AUTONOMY_LEVEL_L1,
    ACTION_CORRECT_LOCAL: AUTONOMY_LEVEL_L2,
    ACTION_REPLAN_LOCAL: AUTONOMY_LEVEL_L3,
    ACTION_ESCALATE: AUTONOMY_LEVEL_L4,
    ACTION_REPLAN_GLOBAL: AUTONOMY_LEVEL_L4,
}


def classify_autonomy_level(action_level: str) -> str:
    """Mappe un action_level V3 vers un niveau d'autonomie V12.3.

    Renvoie L1_absorbed si l'action n'est pas dans la table.
    Cette politique strict-fail-soft est volontaire : un futur niveau
    V3 inconnu doit déclencher la voie la plus permissive (L1) plutôt
    qu'un blocage.
    """
    return _ACTION_TO_LEVEL.get(action_level, AUTONOMY_LEVEL_L1)


_DESCRIPTIONS = {
    AUTONOMY_LEVEL_L1: (
        "Autonome sans ajustement : écart absorbé par le tampon de "
        "régime nominal, aucune action mécanique requise."
    ),
    AUTONOMY_LEVEL_L2: (
        "Ajustement autonome sans humain : V3 actionnel applique une "
        "correction locale (maintenance immédiate, intervention qualité, "
        "source alternatif, fragmentation locale) sans validation."
    ),
    AUTONOMY_LEVEL_L3: (
        "Replanification locale avec validation humaine : le système "
        "propose un replan partiel (un contrat, un goulot) ; l'opérateur "
        "vérifie et approuve avant application."
    ),
    AUTONOMY_LEVEL_L4: (
        "Replanification totale avec validation humaine : l'écart impose "
        "une refonte globale du plan (multi-contrats, plusieurs goulots, "
        "horizon entier) ; le supervisor valide."
    ),
}


def describe_level(level: str) -> str:
    return _DESCRIPTIONS.get(level, f"(niveau inconnu: {level})")
