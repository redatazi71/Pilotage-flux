"""5 flux de visualisation du cadrage v1.3 §12.

Chaque flux est exposé par une fonction `build_<flux>(conn) ->
FluxGraph` qui renvoie un graphe (nodes + edges) destiné à
l'export visuel. Les graphes sont **read-only** : aucune
modification de la base.

  - flux_physique     : matières, OF, lots, WIP, transferts par
                        poste et goulots
  - flux_information  : prévisions, ordres candidats, contrats,
                        événements, KPI
  - flux_decision     : portes P1-P4, decisions, scores, actions
  - flux_documentaire : versions articles, gammes, BOM, contrats
  - flux_qualite      : contrôles, NC, retouches, libérations

Chaque graphe peut être sérialisé (JSON), inséré dans un rapport
ou consommé par un outil de visualisation externe (Graphviz,
Mermaid, D3.js…).
"""

from pilotage_flux.flux_visualization.builders import (
    FluxEdge,
    FluxGraph,
    FluxNode,
    build_all_flux,
    build_flux_decision,
    build_flux_documentaire,
    build_flux_information,
    build_flux_physique,
    build_flux_qualite,
)

__all__ = [
    "FluxEdge",
    "FluxGraph",
    "FluxNode",
    "build_all_flux",
    "build_flux_decision",
    "build_flux_documentaire",
    "build_flux_information",
    "build_flux_physique",
    "build_flux_qualite",
]
