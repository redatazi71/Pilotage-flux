"""Moteur APS V1 : CBN multi-niveau, charge/capacite, pegging, contrats OF."""

from pilotage_flux.aps.bom_flattener import (
    FlatNode,
    flatten_bom_for_article,
    get_manufactured_components,
    get_purchased_components,
    persist_flattened_bom,
)
from pilotage_flux.aps.capacity import compute_load_by_workstation, WorkstationLoad
from pilotage_flux.aps.cbn import compute_candidates, NetRequirement
from pilotage_flux.aps.pegging import (
    PeggingLink,
    add_pegging_link,
    get_incoming,
    get_outgoing,
    get_pegging_chain,
    get_root_demand,
)
from pilotage_flux.aps.planner import promote_candidate_to_of, PlanningResult

__all__ = [
    # CBN / planning
    "compute_candidates",
    "NetRequirement",
    "compute_load_by_workstation",
    "WorkstationLoad",
    "promote_candidate_to_of",
    "PlanningResult",
    # BOM
    "FlatNode",
    "flatten_bom_for_article",
    "get_manufactured_components",
    "get_purchased_components",
    "persist_flattened_bom",
    # Pegging
    "PeggingLink",
    "add_pegging_link",
    "get_incoming",
    "get_outgoing",
    "get_pegging_chain",
    "get_root_demand",
]
