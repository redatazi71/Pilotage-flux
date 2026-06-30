"""Moteur APS V1+V2 : CBN multi-niveau, charge/capacite, pegging, alternatives."""

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
from pilotage_flux.aps.cpm_scheduling import (
    CpmReport,
    OperationNode,
    compute_cpm_for_of,
    compute_makespan,
    list_critical_operations,
)
from pilotage_flux.aps.routing_arbitrage import (
    ArbitrageDecision,
    arbitrate_routing_for_of,
    routing_strategy_of,
)
from pilotage_flux.aps.routing_alternatives import (
    RoutingAlternative,
    WorkstationChoice,
    add_alternative,
    available_workstations_for,
    list_alternatives_for,
    pick_workstation,
)

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
    # Routings alternatifs (V2)
    "RoutingAlternative",
    "WorkstationChoice",
    "add_alternative",
    "available_workstations_for",
    "list_alternatives_for",
    "pick_workstation",
    # CPM (L11.1)
    "CpmReport",
    "OperationNode",
    "compute_cpm_for_of",
    "compute_makespan",
    "list_critical_operations",
    # Arbitrage routing (L11.2)
    "ArbitrageDecision",
    "arbitrate_routing_for_of",
    "routing_strategy_of",
]
