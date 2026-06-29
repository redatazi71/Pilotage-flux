"""Scénarios reproductibles pour l'étude comparative (L4.1 + L5.1).

Un scénario décrit :
  - les commandes initiales (jour 0),
  - l'état initial des stocks et achats ouverts,
  - les aléas datés (panne poste, NC qualité, retard fournisseur, urgence),
  - l'horizon d'exécution en jours logiques,
  - une seed déterministe.

Quatre scénarios canoniques :
  - baseline                 : 4 aléas variés sur 15 jours
  - stress_double_breakdown  : 2 pannes simultanées sur des postes différents
  - stress_cascade_nc        : 3 NC qualité consécutives
  - stress_demand_spike      : 3 urgences clients en pic

Variance multi-seeds : `jitter_scenario(scenario, seed)` applique un bruit
déterministe (timing ±1 jour, magnitude ±20%) pour étudier la stabilité.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from typing import Any


DOCTRINE_OF = "of"
DOCTRINE_FLUX = "flux"
DOCTRINE_OF_EVENT = "of_event"
DOCTRINE_EVENT = "event"
DOCTRINES = (DOCTRINE_OF, DOCTRINE_FLUX, DOCTRINE_OF_EVENT, DOCTRINE_EVENT)


HAZARD_BREAKDOWN = "breakdown_ws"
HAZARD_QUALITY_NC = "quality_nc"
HAZARD_PO_DELAY = "po_delay"
HAZARD_URGENT_ORDER = "urgent_order"


@dataclass(frozen=True)
class HazardEvent:
    day: int
    kind: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class Scenario:
    name: str
    seed: int
    horizon_days: int
    horizon_start: str
    initial_sales_orders: list[dict[str, Any]]
    initial_stocks: dict[str, float]
    initial_purchase_orders: list[dict[str, Any]]
    hazards: list[HazardEvent] = field(default_factory=list)


def _default_sales_orders() -> list[dict[str, Any]]:
    return [
        {"sales_order_id": "SO-001", "article_id": "ART-A",
         "quantity": 100, "due_date": "2026-07-15"},
        {"sales_order_id": "SO-002", "article_id": "ART-A",
         "quantity": 50, "due_date": "2026-07-22"},
        {"sales_order_id": "SO-003", "article_id": "ART-A",
         "quantity": 80, "due_date": "2026-07-22"},
    ]


def _default_stocks() -> dict[str, float]:
    return {"COMP-X": 200.0, "COMP-Y": 120.0}


def _default_pos() -> list[dict[str, Any]]:
    return [
        {"po_id": "PO-0001", "article_id": "COMP-X",
         "qty": 300, "expected_day": 5},
        {"po_id": "PO-0002", "article_id": "COMP-Y",
         "qty": 200, "expected_day": 3},
    ]


def baseline_scenario() -> Scenario:
    """Scénario canonique : 4 aléas variés sur 15 jours."""
    return Scenario(
        name="baseline",
        seed=42,
        horizon_days=15,
        horizon_start="2026-07-06",
        initial_sales_orders=_default_sales_orders(),
        initial_stocks=_default_stocks(),
        initial_purchase_orders=_default_pos(),
        hazards=[
            HazardEvent(day=2, kind=HAZARD_BREAKDOWN,
                        payload={"workstation_id": "WS-2",
                                 "slowdown_factor": 2.0,
                                 "duration_days": 4}),
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


def stress_double_breakdown_scenario() -> Scenario:
    """Stress : deux pannes simultanées sur des postes différents.

    Met à l'épreuve la capacité de V3 à détecter et clore plusieurs
    pannes en parallèle (une sur le goulot WS-3, une sur WS-1).
    """
    return Scenario(
        name="stress_double_breakdown",
        seed=100,
        horizon_days=18,
        horizon_start="2026-07-06",
        initial_sales_orders=_default_sales_orders(),
        initial_stocks=_default_stocks(),
        initial_purchase_orders=_default_pos(),
        hazards=[
            HazardEvent(day=2, kind=HAZARD_BREAKDOWN,
                        payload={"workstation_id": "WS-1",
                                 "slowdown_factor": 2.0,
                                 "duration_days": 5}),
            HazardEvent(day=3, kind=HAZARD_BREAKDOWN,
                        payload={"workstation_id": "WS-3",
                                 "slowdown_factor": 2.0,
                                 "duration_days": 5}),
        ],
    )


def stress_cascade_nc_scenario() -> Scenario:
    """Stress : 3 NC qualité en cascade (jours 2, 3, 4)."""
    return Scenario(
        name="stress_cascade_nc",
        seed=200,
        horizon_days=15,
        horizon_start="2026-07-06",
        initial_sales_orders=_default_sales_orders(),
        initial_stocks=_default_stocks(),
        initial_purchase_orders=_default_pos(),
        hazards=[
            HazardEvent(day=2, kind=HAZARD_QUALITY_NC,
                        payload={"article_id": "SEMI-1",
                                 "qty_scrap": 10,
                                 "severity": "high"}),
            HazardEvent(day=3, kind=HAZARD_QUALITY_NC,
                        payload={"article_id": "ART-A",
                                 "qty_scrap": 12,
                                 "severity": "high"}),
            HazardEvent(day=4, kind=HAZARD_QUALITY_NC,
                        payload={"article_id": "ART-A",
                                 "qty_scrap": 15,
                                 "severity": "critical"}),
        ],
    )


def stress_demand_spike_scenario() -> Scenario:
    """Stress : 3 urgences clients en pic (jours 2, 4, 6)."""
    return Scenario(
        name="stress_demand_spike",
        seed=300,
        horizon_days=15,
        horizon_start="2026-07-06",
        initial_sales_orders=_default_sales_orders(),
        initial_stocks=_default_stocks(),
        initial_purchase_orders=_default_pos(),
        hazards=[
            HazardEvent(day=2, kind=HAZARD_URGENT_ORDER,
                        payload={"sales_order_id": "SO-URG-1",
                                 "article_id": "ART-A",
                                 "quantity": 20,
                                 "due_day": 8}),
            HazardEvent(day=4, kind=HAZARD_URGENT_ORDER,
                        payload={"sales_order_id": "SO-URG-2",
                                 "article_id": "ART-A",
                                 "quantity": 25,
                                 "due_day": 10}),
            HazardEvent(day=6, kind=HAZARD_URGENT_ORDER,
                        payload={"sales_order_id": "SO-URG-3",
                                 "article_id": "ART-A",
                                 "quantity": 30,
                                 "due_day": 12}),
        ],
    )


ALL_SCENARIOS = {
    "baseline": baseline_scenario,
    "stress_double_breakdown": stress_double_breakdown_scenario,
    "stress_cascade_nc": stress_cascade_nc_scenario,
    "stress_demand_spike": stress_demand_spike_scenario,
}


def jitter_scenario(scenario: Scenario, seed: int) -> Scenario:
    """Applique un bruit déterministe à un scénario : timing ±1 jour,
    magnitudes (qty_scrap, quantity, delay_days) ±20%.

    L'horizon, les stocks et les SO initiaux ne sont pas altérés.
    """
    rng = random.Random(seed)
    new_hazards: list[HazardEvent] = []
    for h in scenario.hazards:
        day_offset = rng.choice([-1, 0, 0, 1])
        new_day = max(1, min(scenario.horizon_days - 1, h.day + day_offset))
        payload = dict(h.payload)
        for key in ("qty_scrap", "quantity", "delay_days", "duration_days"):
            if key in payload:
                factor = rng.uniform(0.8, 1.2)
                base = payload[key]
                payload[key] = max(1, int(round(float(base) * factor)))
        new_hazards.append(HazardEvent(day=new_day, kind=h.kind, payload=payload))
    return replace(scenario, hazards=new_hazards, seed=seed)
