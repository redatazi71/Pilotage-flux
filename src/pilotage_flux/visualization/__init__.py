"""Visualisation des 5 familles de flux (§12 cadrage / L6.2).

  1. **physique** : par poste (`workstation_view`) et par OF (`of_detail_view`).
  2. **matière**  : stocks + PO + consommations vs théorique (`material_flow_view`).
  3. **qualité** : contrôles + NC + rebuts par OF (`quality_flow_view`).
  4. **décisionnel** : portes + zones + filtre dual (`decision_flow_view`).
  5. **événementiel** : attendus vs réels + causes (`event_flow_view`).
"""

from pilotage_flux.visualization.flow import (
    OFDetail,
    OperationDetail,
    WorkstationView,
    of_detail_view,
    workstation_view,
)
from pilotage_flux.visualization.material import (
    MaterialFlowItem,
    MaterialFlowReport,
    material_flow_view,
)
from pilotage_flux.visualization.quality import (
    QualityFlowItem,
    QualityFlowReport,
    quality_flow_view,
)
from pilotage_flux.visualization.decision import (
    DecisionFlowReport,
    GateDecisionItem,
    ToleranceActionItem,
    ZoneTransitionItem,
    decision_flow_view,
)
from pilotage_flux.visualization.event import (
    EventFlowReport,
    EventLine,
    event_flow_view,
)


__all__ = [
    # 1. physique
    "workstation_view",
    "WorkstationView",
    "of_detail_view",
    "OperationDetail",
    "OFDetail",
    # 2. matière
    "material_flow_view",
    "MaterialFlowItem",
    "MaterialFlowReport",
    # 3. qualité
    "quality_flow_view",
    "QualityFlowItem",
    "QualityFlowReport",
    # 4. décisionnel
    "decision_flow_view",
    "DecisionFlowReport",
    "GateDecisionItem",
    "ZoneTransitionItem",
    "ToleranceActionItem",
    # 5. événementiel
    "event_flow_view",
    "EventFlowReport",
    "EventLine",
]
