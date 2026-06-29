"""Étude comparative V4 (L4.1 → L4.3) : OF / FLUX / EVENT.

Exécute un même scénario (commandes + aléas datés) selon trois doctrines :
  - "of"    : APS+MES OF-driven (V0) — pas de contrat de flux, pas de freeze,
              pas d'event sourcing. Replanification globale à chaque changement.
  - "flux"  : APS+MES pilotage flux (V1+V2) — contrats de flux, portes
              P2/P3, zones, freeze, stocks/qualité/logistique. Pas de
              régulation événementielle.
  - "event" : APS+MES event sourcing (V3) — V1+V2 + événements attendus,
              matching, filtre dual de tolérances, causes racines, mémoire.

Mesure les KPI du §19 du cadrage et publie un rapport comparatif §24.
"""

from pilotage_flux.comparative.scenario import (
    DOCTRINE_EVENT,
    DOCTRINE_FLUX,
    DOCTRINE_OF,
    DOCTRINES,
    HazardEvent,
    Scenario,
    baseline_scenario,
)
from pilotage_flux.comparative.runner import RunResult, run_doctrine
from pilotage_flux.comparative.kpis import KpiSet, compute_kpis
from pilotage_flux.comparative.report import build_comparative_report

__all__ = [
    "DOCTRINE_EVENT",
    "DOCTRINE_FLUX",
    "DOCTRINE_OF",
    "DOCTRINES",
    "HazardEvent",
    "Scenario",
    "baseline_scenario",
    "RunResult",
    "run_doctrine",
    "KpiSet",
    "compute_kpis",
    "build_comparative_report",
]
