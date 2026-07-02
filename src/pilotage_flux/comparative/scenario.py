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
DOCTRINE_OF_MILP = "of_milp"
# Ext-l — Baseline « CP-SAT réactif » : re-solve CP-SAT à chaque événement
# significatif (hazard). Réponse frontale à l'objection reviewer
# « pourquoi pas juste re-solve OR-Tools à chaque event ? ». Attendu :
# coûteux, instable (nervosité pire qu'OF), OTIF marginal — preuve
# qu'event-driven ≠ ré-optimisation permanente.
DOCTRINE_OF_REACTIVE_CPSAT = "of_reactive_cpsat"
# BCE (Boucle Cybernétique Étendue) — pilotages 5 et 6 du banc
# 6-pilotages. Activent la chaîne MACRS Couche 2 + moteur Delta
# (B.1/B.2/B.3) + propagation hazard étiquetée (C.1/C.2). Les
# fondations OF_EVENT / EVENT restent identiques ; le BCE est un
# overlay piloté par le paramètre `bce_enabled` (lu par
# `_apply_hazard` pour déclencher `emit_hazard`).
DOCTRINE_OF_EVENT_BCE = "of_event_bce"
DOCTRINE_EVENT_BCE = "event_bce"
# §7.1 — Variante OF avec planification CP-SAT pour lever le biais
# d'implémentation. Identique à OF (pas de flux, pas d'event sourcing)
# mais étale les OFs sur l'horizon via solveur global au lieu de tout
# lancer au jour 0.
DOCTRINES = (DOCTRINE_OF, DOCTRINE_FLUX, DOCTRINE_OF_EVENT, DOCTRINE_EVENT)
DOCTRINES_WITH_MILP = DOCTRINES + (DOCTRINE_OF_MILP,)
# Tuple étendu pour les études §7.1 — réservé aux scripts qui veulent
# inclure OF_MILP. Le tuple DOCTRINES standard reste à 4 pour ne pas
# casser les tests d'acceptation V0-V11.

# Banc 6-pilotages cible (OF, OF+EVENT, OF+EVENT+BCE, FLUX,
# FLUX+EVENT, FLUX+EVENT+BCE). Réservé aux nouveaux scripts ; ne
# remplace pas `DOCTRINES`.
BCE_DOCTRINES = (DOCTRINE_OF_EVENT_BCE, DOCTRINE_EVENT_BCE)
DOCTRINES_6_PILOTAGES = (
    DOCTRINE_OF, DOCTRINE_OF_EVENT, DOCTRINE_OF_EVENT_BCE,
    DOCTRINE_FLUX, DOCTRINE_EVENT, DOCTRINE_EVENT_BCE,
)


def is_bce_doctrine(doctrine: str) -> bool:
    """True si la doctrine active la couche cybernétique BCE."""
    return doctrine in BCE_DOCTRINES


HAZARD_BREAKDOWN = "breakdown_ws"
HAZARD_QUALITY_NC = "quality_nc"
HAZARD_PO_DELAY = "po_delay"
HAZARD_URGENT_ORDER = "urgent_order"
HAZARD_LOGISTIC_DELAY = "logistic_delay"
# §24.9 — Logistique : un poste de travail est complètement bloqué
# (flux logistique interrompu) pendant N jours. Sémantique distincte
# du breakdown machine : ce n'est pas la machine qui tombe en panne,
# c'est le flux entrant qui ne lui parvient pas.


@dataclass(frozen=True)
class HazardEvent:
    day: int
    kind: str
    payload: dict[str, Any]
    # Étiquetage causal (C.1) : racine MACRS R001..R046 et catégorie Δ
    # qui correspondent au mécanisme du hazard. Optionnels — si None,
    # la résolution par défaut s'opère via
    # `cybernetic.macrs.hazard_labels.resolve_racine`.
    racine_id: str | None = None
    categorie_code: str | None = None


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


def _default_xl_sales_orders() -> list[dict[str, Any]]:
    """Set étendu : 6 SO sur 4 articles finis (ART-A/B/C/D)."""
    return [
        {"sales_order_id": "SO-001", "article_id": "ART-A",
         "quantity": 80,  "due_date": "2026-07-20"},
        {"sales_order_id": "SO-002", "article_id": "ART-A",
         "quantity": 60,  "due_date": "2026-07-25"},
        {"sales_order_id": "SO-003", "article_id": "ART-B",
         "quantity": 120, "due_date": "2026-07-22"},
        {"sales_order_id": "SO-004", "article_id": "ART-B",
         "quantity": 90,  "due_date": "2026-07-28"},
        {"sales_order_id": "SO-005", "article_id": "ART-C",
         "quantity": 100, "due_date": "2026-07-25"},
        {"sales_order_id": "SO-006", "article_id": "ART-D",
         "quantity": 70,  "due_date": "2026-07-26"},
    ]


def _default_xl_stocks() -> dict[str, float]:
    return {
        "COMP-X": 600.0, "COMP-Y": 250.0, "COMP-Z": 500.0,
        "COMP-W": 800.0, "COMP-V": 300.0,
    }


def _default_xl_pos() -> list[dict[str, Any]]:
    return [
        {"po_id": "PO-0001", "article_id": "COMP-X", "qty": 500, "expected_day": 5},
        {"po_id": "PO-0002", "article_id": "COMP-Y", "qty": 200, "expected_day": 4},
        {"po_id": "PO-0003", "article_id": "COMP-Z", "qty": 400, "expected_day": 6},
        {"po_id": "PO-0004", "article_id": "COMP-W", "qty": 600, "expected_day": 3},
        {"po_id": "PO-0005", "article_id": "COMP-V", "qty": 300, "expected_day": 7},
    ]


def baseline_xl_scenario() -> Scenario:
    """Scénario étendu baseline : 6 SO sur 4 articles finis, 4 aléas variés.

    Sur fixtures_extended (4 finis × 4 semi × 5 composants, 6 postes).
    Exerce le multi-contrats P3 collective.
    """
    return Scenario(
        name="baseline_xl",
        seed=42,
        horizon_days=20,
        horizon_start="2026-07-06",
        initial_sales_orders=_default_xl_sales_orders(),
        initial_stocks=_default_xl_stocks(),
        initial_purchase_orders=_default_xl_pos(),
        hazards=[
            HazardEvent(day=2, kind=HAZARD_BREAKDOWN,
                        payload={"workstation_id": "WS-3",  # goulot
                                 "slowdown_factor": 2.0,
                                 "duration_days": 4}),
            HazardEvent(day=3, kind=HAZARD_QUALITY_NC,
                        payload={"article_id": "ART-A",
                                 "qty_scrap": 20, "severity": "high"}),
            HazardEvent(day=4, kind=HAZARD_PO_DELAY,
                        payload={"po_id": "PO-0001", "delay_days": 7}),
            HazardEvent(day=6, kind=HAZARD_URGENT_ORDER,
                        payload={"sales_order_id": "SO-URG-XL",
                                 "article_id": "ART-B",
                                 "quantity": 40, "due_day": 12}),
        ],
    )


def stress_double_breakdown_xl_scenario() -> Scenario:
    """Stress XL : 2 pannes sur des postes critiques (WS-3 goulot + WS-4)."""
    return Scenario(
        name="stress_double_breakdown_xl",
        seed=100,
        horizon_days=22,
        horizon_start="2026-07-06",
        initial_sales_orders=_default_xl_sales_orders(),
        initial_stocks=_default_xl_stocks(),
        initial_purchase_orders=_default_xl_pos(),
        hazards=[
            HazardEvent(day=2, kind=HAZARD_BREAKDOWN,
                        payload={"workstation_id": "WS-3",
                                 "slowdown_factor": 2.0,
                                 "duration_days": 5}),
            HazardEvent(day=3, kind=HAZARD_BREAKDOWN,
                        payload={"workstation_id": "WS-4",
                                 "slowdown_factor": 2.0,
                                 "duration_days": 5}),
        ],
    )


def stress_cascade_nc_xl_scenario() -> Scenario:
    """Stress XL : 4 NCs en cascade sur 3 articles."""
    return Scenario(
        name="stress_cascade_nc_xl",
        seed=200,
        horizon_days=20,
        horizon_start="2026-07-06",
        initial_sales_orders=_default_xl_sales_orders(),
        initial_stocks=_default_xl_stocks(),
        initial_purchase_orders=_default_xl_pos(),
        hazards=[
            HazardEvent(day=2, kind=HAZARD_QUALITY_NC,
                        payload={"article_id": "SEMI-1",
                                 "qty_scrap": 15, "severity": "high"}),
            HazardEvent(day=3, kind=HAZARD_QUALITY_NC,
                        payload={"article_id": "ART-A",
                                 "qty_scrap": 18, "severity": "high"}),
            HazardEvent(day=4, kind=HAZARD_QUALITY_NC,
                        payload={"article_id": "ART-B",
                                 "qty_scrap": 22, "severity": "critical"}),
            HazardEvent(day=5, kind=HAZARD_QUALITY_NC,
                        payload={"article_id": "ART-C",
                                 "qty_scrap": 15, "severity": "high"}),
        ],
    )


def stress_demand_spike_xl_scenario() -> Scenario:
    """Stress XL : 5 urgences clients sur des articles variés."""
    return Scenario(
        name="stress_demand_spike_xl",
        seed=300,
        horizon_days=22,
        horizon_start="2026-07-06",
        initial_sales_orders=_default_xl_sales_orders(),
        initial_stocks=_default_xl_stocks(),
        initial_purchase_orders=_default_xl_pos(),
        hazards=[
            HazardEvent(day=2, kind=HAZARD_URGENT_ORDER,
                        payload={"sales_order_id": "SO-URG-1",
                                 "article_id": "ART-A",
                                 "quantity": 30, "due_day": 9}),
            HazardEvent(day=3, kind=HAZARD_URGENT_ORDER,
                        payload={"sales_order_id": "SO-URG-2",
                                 "article_id": "ART-B",
                                 "quantity": 40, "due_day": 11}),
            HazardEvent(day=5, kind=HAZARD_URGENT_ORDER,
                        payload={"sales_order_id": "SO-URG-3",
                                 "article_id": "ART-D",
                                 "quantity": 25, "due_day": 13}),
            HazardEvent(day=7, kind=HAZARD_URGENT_ORDER,
                        payload={"sales_order_id": "SO-URG-4",
                                 "article_id": "ART-C",
                                 "quantity": 35, "due_day": 15}),
            HazardEvent(day=9, kind=HAZARD_URGENT_ORDER,
                        payload={"sales_order_id": "SO-URG-5",
                                 "article_id": "ART-A",
                                 "quantity": 50, "due_day": 17}),
        ],
    )


def stress_multi_contract_overload_scenario() -> Scenario:
    """Stress XL : forte demande simultanée sur les 4 articles finis avec
    horizon serré, forçant la P3 collective à arbitrer entre contrats.

    La charge cumulée sur le goulot WS-3 dépasse sa capacité d'horizon
    (~7392 min sur 11 jours ouvrés × 960 × 0.70). Charge demandée :
    1200 + 1500 + 800 + 1200 = 4700 min sur WS-3 — dans l'enveloppe.
    Pour forcer PARTIAL_FREEZE, on raccourcit l'horizon à 7 jours (~5
    jours ouvrés ≈ 3360 min de capacité).
    """
    return Scenario(
        name="stress_multi_contract_overload",
        seed=400,
        horizon_days=7,   # horizon serré → capacité goulot réduite
        horizon_start="2026-07-06",
        initial_sales_orders=[
            {"sales_order_id": "SO-A1", "article_id": "ART-A",
             "quantity": 200, "due_date": "2026-07-13"},
            {"sales_order_id": "SO-A2", "article_id": "ART-A",
             "quantity": 150, "due_date": "2026-07-13"},
            {"sales_order_id": "SO-B1", "article_id": "ART-B",
             "quantity": 300, "due_date": "2026-07-13"},
            {"sales_order_id": "SO-B2", "article_id": "ART-B",
             "quantity": 200, "due_date": "2026-07-13"},
            {"sales_order_id": "SO-C1", "article_id": "ART-C",
             "quantity": 250, "due_date": "2026-07-13"},
            {"sales_order_id": "SO-C2", "article_id": "ART-C",
             "quantity": 200, "due_date": "2026-07-13"},
            {"sales_order_id": "SO-D1", "article_id": "ART-D",
             "quantity": 150, "due_date": "2026-07-13"},
            {"sales_order_id": "SO-D2", "article_id": "ART-D",
             "quantity": 100, "due_date": "2026-07-13"},
        ],
        initial_stocks={
            "COMP-X": 2500.0, "COMP-Y": 1000.0, "COMP-Z": 2000.0,
            "COMP-W": 3000.0, "COMP-V": 1200.0,
        },
        initial_purchase_orders=[
            {"po_id": "PO-0001", "article_id": "COMP-X",
             "qty": 1500, "expected_day": 4},
            {"po_id": "PO-0002", "article_id": "COMP-Y",
             "qty": 700, "expected_day": 3},
        ],
        hazards=[
            HazardEvent(day=2, kind=HAZARD_BREAKDOWN,
                        payload={"workstation_id": "WS-3",
                                 "slowdown_factor": 1.5,
                                 "duration_days": 2}),
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


# Scénarios étendus (fixtures_extended, multi-articles, multi-contrats)
ALL_SCENARIOS_XL = {
    "baseline_xl": baseline_xl_scenario,
    "stress_double_breakdown_xl": stress_double_breakdown_xl_scenario,
    "stress_cascade_nc_xl": stress_cascade_nc_xl_scenario,
    "stress_demand_spike_xl": stress_demand_spike_xl_scenario,
    "stress_multi_contract_overload": stress_multi_contract_overload_scenario,
}


ALL_SCENARIOS_ANY = {**ALL_SCENARIOS, **ALL_SCENARIOS_XL}


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
