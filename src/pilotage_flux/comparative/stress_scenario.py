"""Scénarios stress pour discrimination des pilotages.

Le scenario baseline (15j, 4 hazards) plafonne à OTIF 100% pour
tous les pilotages : aucune discrimination possible. Ce module
fournit des scénarios stress qui forcent les pilotages à sortir
de la zone confortable.

Caractéristiques d'un scénario stress :
  - **Horizon 60 jours** au lieu de 15 → ré-arrangements possibles
  - **12 hazards** au lieu de 4 → pression continue
  - **Mix hazards** : 4 BREAKDOWN, 3 QUALITY_NC, 2 PO_DELAY,
                      2 URGENT_ORDER, 1 LOGISTIC_DELAY
  - **Séquence en cascade** : hazards rapprochés (tous les 5 jours)
  - **Reproducibilité par seed** : la position exacte des hazards
    et leur payload varient selon le seed, mais le total reste
    déterministe

Pour la saturation étendue (au-delà de 100%), voir
SATURATION_TARGETS_STRESS.
"""

from __future__ import annotations

import random
from dataclasses import replace

from pilotage_flux.comparative.scenario import (
    HAZARD_BREAKDOWN,
    HAZARD_LOGISTIC_DELAY,
    HAZARD_PO_DELAY,
    HAZARD_QUALITY_NC,
    HAZARD_URGENT_ORDER,
    HazardEvent,
    Scenario,
    baseline_scenario,
)


# Saturations étendues : on pousse au-delà de 100% pour forcer la
# rupture. 110% = 10% de surcharge cumulative — typique d'un régime
# de pic conjoncturel.
SATURATION_TARGETS_STRESS: tuple[float, ...] = (
    0.78, 0.86, 0.94, 1.00, 1.05, 1.10,
)


# Plan canonique des 12 hazards d'un scénario stress :
#   (jour_relatif, kind, payload_template)
_STRESS_HAZARD_PLAN: tuple[tuple[int, str, dict], ...] = (
    (3,  HAZARD_BREAKDOWN,      {"workstation_id": "WS-2",
                                  "duration_days": 2,
                                  "slowdown_factor": 2.0}),
    (8,  HAZARD_QUALITY_NC,     {"article_id": "ART-A",
                                  "qty_scrap": 10,
                                  "severity": "normal"}),
    (12, HAZARD_BREAKDOWN,      {"workstation_id": "WS-3",
                                  "duration_days": 2,
                                  "slowdown_factor": 1.8}),
    (17, HAZARD_PO_DELAY,       {"po_id": "PO-0001",
                                  "delay_days": 3}),
    (22, HAZARD_URGENT_ORDER,   {"sales_order_id": "SO-URG-1",
                                  "article_id": "ART-A",
                                  "quantity": 30,
                                  "due_day": 28}),
    (28, HAZARD_QUALITY_NC,     {"article_id": "ART-A",
                                  "qty_scrap": 8,
                                  "severity": "normal"}),
    (33, HAZARD_BREAKDOWN,      {"workstation_id": "WS-1",
                                  "duration_days": 1,
                                  "slowdown_factor": 1.5}),
    (38, HAZARD_LOGISTIC_DELAY, {"workstation_id": "WS-2",
                                  "block_days": 2}),
    (43, HAZARD_PO_DELAY,       {"po_id": "PO-0002",
                                  "delay_days": 4}),
    (48, HAZARD_URGENT_ORDER,   {"sales_order_id": "SO-URG-2",
                                  "article_id": "ART-A",
                                  "quantity": 25,
                                  "due_day": 53}),
    (52, HAZARD_QUALITY_NC,     {"article_id": "ART-A",
                                  "qty_scrap": 12,
                                  "severity": "severe"}),
    (56, HAZARD_BREAKDOWN,      {"workstation_id": "WS-2",
                                  "duration_days": 2,
                                  "slowdown_factor": 2.2}),
)


def stress_scenario(
    *,
    seed: int = 42,
    horizon_days: int = 60,
    seed_jitter: bool = True,
    name: str | None = None,
) -> Scenario:
    """Construit un scénario stress de 60 jours / 12 hazards.

    Si `seed_jitter`, les jours d'apparition des hazards sont
    légèrement décalés selon le seed (±2 jours) pour générer de la
    variance inter-seeds — sans changer le mix ni l'intensité.
    Les payloads (qty_scrap, delay_days...) varient aussi de ±20%.

    Renvoie un Scenario prêt à être consommé par run_doctrine.
    """
    rng = random.Random(seed)
    base = baseline_scenario()

    hazards: list[HazardEvent] = []
    for day_rel, kind, payload_template in _STRESS_HAZARD_PLAN:
        # Jitter du jour
        day = day_rel
        if seed_jitter:
            jitter = rng.randint(-2, 2)
            day = max(1, min(horizon_days - 1, day_rel + jitter))
        # Jitter du payload numérique
        payload = dict(payload_template)
        if seed_jitter:
            for k, v in list(payload.items()):
                if isinstance(v, (int, float)) and k != "due_day":
                    factor = 1 + (rng.random() - 0.5) * 0.4   # ±20%
                    if isinstance(v, int):
                        payload[k] = max(1, int(round(v * factor)))
                    else:
                        payload[k] = max(0.1, v * factor)
        hazards.append(HazardEvent(
            day=day, kind=kind, payload=payload,
        ))

    return replace(
        base,
        name=name or f"stress_seed{seed}",
        seed=seed,
        horizon_days=horizon_days,
        hazards=hazards,
    )


def stress_scenario_count_by_kind() -> dict[str, int]:
    """Distribution canonique des kinds de hazards dans un stress."""
    counts: dict[str, int] = {}
    for _, kind, _ in _STRESS_HAZARD_PLAN:
        counts[kind] = counts.get(kind, 0) + 1
    return counts
