"""Scénario reproductible pour l'étude comparative (L4.1).

Un scénario décrit :
  - les commandes initiales (jour 0),
  - l'état initial des stocks et achats ouverts,
  - les aléas datés (panne poste, NC qualité, retard fournisseur, urgence),
  - l'horizon d'exécution en jours logiques,
  - une seed déterministe.

Format Python pour rester typé. Sérialisable en JSON via dataclasses.asdict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


DOCTRINE_OF = "of"
DOCTRINE_FLUX = "flux"
DOCTRINE_EVENT = "event"
DOCTRINES = (DOCTRINE_OF, DOCTRINE_FLUX, DOCTRINE_EVENT)


HAZARD_BREAKDOWN = "breakdown_ws"
HAZARD_QUALITY_NC = "quality_nc"
HAZARD_PO_DELAY = "po_delay"
HAZARD_URGENT_ORDER = "urgent_order"


@dataclass(frozen=True)
class HazardEvent:
    """Aléa daté appliqué pendant l'exécution d'un scénario.

    kind ∈ {breakdown_ws, quality_nc, po_delay, urgent_order}.
    payload : sémantique propre à chaque kind (cf. runner.py).
    """

    day: int
    kind: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class Scenario:
    name: str
    seed: int
    horizon_days: int
    horizon_start: str  # ISO date pour ancrer les datetimes logiques
    initial_sales_orders: list[dict[str, Any]]
    initial_stocks: dict[str, float]
    initial_purchase_orders: list[dict[str, Any]]
    hazards: list[HazardEvent] = field(default_factory=list)


def baseline_scenario() -> Scenario:
    """Scénario canonique de l'étude V4.

    Setup :
      - 3 commandes ART-A (100, 50, 80 pcs) jour 0.
      - Stocks COMP-X 200, COMP-Y 120. 2 PO COMP-X (300, jour +5) et COMP-Y (200, jour +3).

    Aléas :
      - Jour 2 : panne WS-2 (slowdown ×1.5 pendant 1 jour).
      - Jour 3 : NC qualité 15 pcs scrap sur l'OF en cours sur ART-A.
      - Jour 4 : retard PO COMP-X de +7 jours.
      - Jour 5 : urgence client : SO supplémentaire 30 pcs ART-A dû jour 8.
    """
    return Scenario(
        name="baseline",
        seed=42,
        horizon_days=10,
        horizon_start="2026-07-06",
        initial_sales_orders=[
            {"sales_order_id": "SO-001", "article_id": "ART-A",
             "quantity": 100, "due_date": "2026-07-15"},
            {"sales_order_id": "SO-002", "article_id": "ART-A",
             "quantity": 50, "due_date": "2026-07-22"},
            {"sales_order_id": "SO-003", "article_id": "ART-A",
             "quantity": 80, "due_date": "2026-07-22"},
        ],
        initial_stocks={"COMP-X": 200.0, "COMP-Y": 120.0},
        initial_purchase_orders=[
            {"po_id": "PO-0001", "article_id": "COMP-X",
             "qty": 300, "expected_day": 5},
            {"po_id": "PO-0002", "article_id": "COMP-Y",
             "qty": 200, "expected_day": 3},
        ],
        hazards=[
            HazardEvent(day=2, kind=HAZARD_BREAKDOWN,
                        payload={"workstation_id": "WS-2",
                                 "slowdown_factor": 1.5,
                                 "duration_days": 1}),
            HazardEvent(day=3, kind=HAZARD_QUALITY_NC,
                        payload={"article_id": "ART-A",
                                 "qty_scrap": 15,
                                 "severity": "high"}),
            HazardEvent(day=4, kind=HAZARD_PO_DELAY,
                        payload={"po_id": "PO-0001",
                                 "delay_days": 7}),
            HazardEvent(day=5, kind=HAZARD_URGENT_ORDER,
                        payload={"sales_order_id": "SO-URG",
                                 "article_id": "ART-A",
                                 "quantity": 30,
                                 "due_day": 8}),
        ],
    )
