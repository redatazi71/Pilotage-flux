"""V12.5 — Matrice d'orchestration (dernière brique V12).

Cette couche unifie les 4 briques V12 précédentes (forecasting V12.1,
optim V12.2, delta engine V12.3, human loop V12.4) en exposant :

  - **WorkshopProfile** : configuration runtime data-driven de
    tous les seuils, fenêtres, politiques (sérialisable JSON)

  - **3 profils défaut** : SMALL_PROFILE, MEDIUM_PROFILE, LARGE_PROFILE
    correspondant à 3 tailles d'atelier (10-20, 20-50, 50+ OFs)

  - **OrchestrationContext** : état courant agrégé (charge, pending,
    rejets récents, biais historique)

  - **OrchestrationMatrix** : sélecteur automatique de l'algorithme
    selon contexte × profil
      select_optimizer(ctx)   → cp_sat_dynamic | heuristic_atc | …
      select_forecaster(ctx)  → ensemble_inv_rmse | hazard_aware | …
      autonomy_thresholds()   → seuils L1/L2/L3/L4 effectifs

La matrice n'invente pas de nouvelle décision — elle agrège des
heuristiques de sélection paramétrables qui se substituent aux
défauts en dur des modules précédents.
"""

from pilotage_flux.cybernetic.orchestration.matrix import (
    OrchestrationContext,
    OrchestrationMatrix,
)
from pilotage_flux.cybernetic.orchestration.profile import (
    DEFAULT_PROFILES,
    LARGE_PROFILE,
    MEDIUM_PROFILE,
    SMALL_PROFILE,
    WorkshopProfile,
    load_profile,
    save_profile,
)

__all__ = [
    "WorkshopProfile",
    "SMALL_PROFILE",
    "MEDIUM_PROFILE",
    "LARGE_PROFILE",
    "DEFAULT_PROFILES",
    "load_profile",
    "save_profile",
    "OrchestrationContext",
    "OrchestrationMatrix",
]
