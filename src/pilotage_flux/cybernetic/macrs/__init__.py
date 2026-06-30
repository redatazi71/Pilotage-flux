"""MACRS — Matrice d'Analyse des Causes Racines Structurée.

Référence : matrice_incidence_causale.md (Couche 1) +
            matrice_operationnelle_specification.md (Couche 2).

Couche 1 (statique) : `macrs_racines` × `macrs_categories` × `macrs_incidence`
    — 46 racines R001..R046, 7 catégories Δ, 175 cellules actives.

Couche 2 (dynamique) : `causal_cells` + agrégats temporels
    — alimente le Pareto hiérarchique et le moteur Delta.
"""

from pilotage_flux.cybernetic.macrs.couche1 import (
    CATEGORIES,
    RACINES,
    seed_macrs_layer1,
    count_incidences,
    list_racines,
    list_categories,
    get_incidences_for_racine,
    get_racines_for_category,
)
from pilotage_flux.cybernetic.macrs.couche2 import (
    K_DEFAULT,
    STATUS_ACTIVE,
    STATUS_INCOMING,
    STATUS_OBSERVING,
    STATUSES,
    CausalCell,
    count_cells_by_status,
    get_cell,
    get_k_for_subdomain,
    init_cells_from_layer1,
    list_cells_by_status,
    record_event,
)

__all__ = [
    "CATEGORIES",
    "RACINES",
    "seed_macrs_layer1",
    "count_incidences",
    "list_racines",
    "list_categories",
    "get_incidences_for_racine",
    "get_racines_for_category",
    "K_DEFAULT",
    "STATUS_ACTIVE",
    "STATUS_INCOMING",
    "STATUS_OBSERVING",
    "STATUSES",
    "CausalCell",
    "count_cells_by_status",
    "get_cell",
    "get_k_for_subdomain",
    "init_cells_from_layer1",
    "list_cells_by_status",
    "record_event",
]
