"""Ext-g — Cascade d'aléas avec corrélations temporelles.

Modélise les corrélations plausibles entre types d'aléas industriels.
Une panne machine (BREAKDOWN) *augmente* la probabilité d'un retard
composant (PO_DELAY) dans les jours suivants — l'inverse est également
vrai. Ces corrélations reflètent la réalité opérationnelle :
un incident déclenche souvent une cascade.

Cinq **profils cascade** doctrinaux :

- `mecanique_to_supply` — panne machine → retard fournisseur
  (les composants stockés en attente sont réutilisés plus tôt,
  la re-commande arrive plus tard que prévu).
- `qualite_to_rerun` — NC qualité → panne ou blocage
  (le rework mobilise les postes en secondaire, la ligne se sature).
- `supply_to_urgent` — retard PO → commande urgente
  (client relance faute de composant, priorité ré-attribuée en direct).
- `humain_to_qualite` — panne → NC qualité
  (redémarrage post-panne produit plus de défauts, phase 0 non stable).
- `tempete` — multi-source corrélée forte
  (crise en amont : un cluster de causes déclenche tout à la fois).

Chaque **kernel** ajoute au scénario généré un ou plusieurs aléas fils
tirés dans une fenêtre de délai, avec un multiplicateur de probabilité.
"""

from __future__ import annotations

import random as _random
from dataclasses import dataclass, field
from typing import Any

from pilotage_flux.comparative.scenario import (
    HAZARD_BREAKDOWN,
    HAZARD_LOGISTIC_DELAY,
    HAZARD_PO_DELAY,
    HAZARD_QUALITY_NC,
    HAZARD_URGENT_ORDER,
    HazardEvent,
)


@dataclass(frozen=True)
class CorrelationKernel:
    """Une règle de propagation événementielle.

    Si un aléa de type `source_kind` survient au jour J, avec probabilité
    `prob_trigger` on émet un aléa fils de type `target_kind` dans la
    fenêtre `[J + delay_min, J + delay_max]`.

    `intensity_multiplier` peut être utilisé côté payload pour amplifier
    les paramètres du fils (durée, quantité, etc.) — le runner en tient
    compte s'il est présent.
    """
    name: str
    source_kind: str
    target_kind: str
    delay_min: int
    delay_max: int
    prob_trigger: float          # 0..1 — probabilité d'émission du fils
    intensity_multiplier: float = 1.0


PROFILE_MECANIQUE_TO_SUPPLY = [
    CorrelationKernel(
        "mecanique→supply", HAZARD_BREAKDOWN, HAZARD_PO_DELAY,
        delay_min=2, delay_max=5, prob_trigger=0.6, intensity_multiplier=1.2,
    ),
]

PROFILE_QUALITE_TO_RERUN = [
    CorrelationKernel(
        "qualite→rerun", HAZARD_QUALITY_NC, HAZARD_BREAKDOWN,
        delay_min=0, delay_max=2, prob_trigger=0.5, intensity_multiplier=1.0,
    ),
    CorrelationKernel(
        "qualite→logistique", HAZARD_QUALITY_NC, HAZARD_LOGISTIC_DELAY,
        delay_min=1, delay_max=3, prob_trigger=0.4, intensity_multiplier=1.0,
    ),
]

PROFILE_SUPPLY_TO_URGENT = [
    CorrelationKernel(
        "supply→urgent", HAZARD_PO_DELAY, HAZARD_URGENT_ORDER,
        delay_min=3, delay_max=7, prob_trigger=0.55, intensity_multiplier=1.3,
    ),
]

PROFILE_HUMAIN_TO_QUALITE = [
    CorrelationKernel(
        "humain→qualite", HAZARD_BREAKDOWN, HAZARD_QUALITY_NC,
        delay_min=0, delay_max=1, prob_trigger=0.45, intensity_multiplier=1.15,
    ),
]

PROFILE_TEMPETE = [
    # Multi-source : tout événement amplifie tout autre événement.
    CorrelationKernel(
        "storm-mec→sup", HAZARD_BREAKDOWN, HAZARD_PO_DELAY,
        delay_min=1, delay_max=3, prob_trigger=0.75, intensity_multiplier=1.5,
    ),
    CorrelationKernel(
        "storm-sup→urg", HAZARD_PO_DELAY, HAZARD_URGENT_ORDER,
        delay_min=1, delay_max=4, prob_trigger=0.7, intensity_multiplier=1.5,
    ),
    CorrelationKernel(
        "storm-qua→mec", HAZARD_QUALITY_NC, HAZARD_BREAKDOWN,
        delay_min=0, delay_max=2, prob_trigger=0.6, intensity_multiplier=1.3,
    ),
    CorrelationKernel(
        "storm-mec→qua", HAZARD_BREAKDOWN, HAZARD_QUALITY_NC,
        delay_min=0, delay_max=1, prob_trigger=0.55, intensity_multiplier=1.3,
    ),
]

CASCADE_PROFILES: dict[str, list[CorrelationKernel]] = {
    "mecanique_to_supply": PROFILE_MECANIQUE_TO_SUPPLY,
    "qualite_to_rerun": PROFILE_QUALITE_TO_RERUN,
    "supply_to_urgent": PROFILE_SUPPLY_TO_URGENT,
    "humain_to_qualite": PROFILE_HUMAIN_TO_QUALITE,
    "tempete": PROFILE_TEMPETE,
}


@dataclass
class CascadeStats:
    profile: str
    n_source_events: int = 0
    n_cascade_added: int = 0
    kernel_fired: dict[str, int] = field(default_factory=dict)


def _synthesize_child_payload(
    parent: HazardEvent,
    kernel: CorrelationKernel,
    rng: _random.Random,
) -> dict[str, Any]:
    """Construit un payload minimal cohérent pour le fils, en réutilisant
    les cibles du parent quand c'est pertinent (même workstation_id,
    même article_id, etc.). Sinon on met des valeurs par défaut prudentes.
    """
    parent_pl = parent.payload or {}
    imult = kernel.intensity_multiplier
    tk = kernel.target_kind

    if tk == HAZARD_BREAKDOWN:
        return {
            "workstation_id": parent_pl.get("workstation_id", "WS-CASCADE"),
            "slowdown_factor": round(1.8 * imult, 2),
            "duration_days": max(1, int(2 * imult)),
        }
    if tk == HAZARD_QUALITY_NC:
        return {
            "article_id": parent_pl.get("article_id", "ART-A"),
            "qty_scrap": max(1, int(rng.randint(5, 20) * imult)),
            "severity": "high" if imult >= 1.3 else "normal",
        }
    if tk == HAZARD_PO_DELAY:
        return {
            "po_id": parent_pl.get("po_id", "PO-CASCADE"),
            "delay_days": max(2, int(rng.randint(3, 8) * imult)),
        }
    if tk == HAZARD_URGENT_ORDER:
        parent_day = parent.day
        return {
            "sales_order_id": f"SO-URG-CASC-{parent_day}",
            "article_id": parent_pl.get("article_id", "ART-A"),
            "quantity": max(1, int(rng.randint(10, 40) * imult)),
            "due_day": parent_day + rng.randint(4, 8),
        }
    if tk == HAZARD_LOGISTIC_DELAY:
        return {
            "workstation_id": parent_pl.get("workstation_id", "WS-CASCADE"),
            "block_days": max(1, int(rng.randint(1, 3) * imult)),
        }
    return {}


def apply_correlations(
    hazards: list[HazardEvent],
    profile: str | list[CorrelationKernel],
    seed: int,
    horizon_days: int,
    max_cascade_depth: int = 2,
) -> tuple[list[HazardEvent], CascadeStats]:
    """Applique un ou plusieurs kernels sur une liste d'aléas indépendants
    et retourne la liste enrichie + statistiques de cascade.

    - `profile` : nom d'un profil dans `CASCADE_PROFILES` ou liste directe
      de `CorrelationKernel`.
    - `max_cascade_depth` : profondeur maximale de propagation (défaut 2).
      Empêche les cascades infinies (un fils qui devient parent, etc.).
    - Les aléas fils sont marqués `payload['cascade_origin'] = parent_day`
      pour audit ultérieur.

    Note : la corrélation ne supprime jamais un aléa parent, elle
    n'ajoute que des fils. On garde donc les tirages indépendants intacts
    et on peut comparer proprement « sans corrélation vs avec ».
    """
    if isinstance(profile, str):
        kernels = CASCADE_PROFILES.get(profile, [])
        profile_name = profile
    else:
        kernels = profile
        profile_name = "custom"
    stats = CascadeStats(profile=profile_name)
    if not kernels:
        return list(hazards), stats

    rng = _random.Random(seed ^ 0xC0FFEE)
    result: list[HazardEvent] = list(hazards)
    frontier: list[tuple[HazardEvent, int]] = [(h, 0) for h in hazards]

    while frontier:
        parent, depth = frontier.pop(0)
        if depth >= max_cascade_depth:
            continue
        for k in kernels:
            if parent.kind != k.source_kind:
                continue
            stats.n_source_events += 1
            if rng.random() > k.prob_trigger:
                continue
            delay = rng.randint(k.delay_min, k.delay_max)
            child_day = parent.day + delay
            if child_day >= horizon_days:
                continue
            child = HazardEvent(
                day=child_day,
                kind=k.target_kind,
                payload={
                    **_synthesize_child_payload(parent, k, rng),
                    "cascade_origin_day": parent.day,
                    "cascade_kernel": k.name,
                },
            )
            result.append(child)
            stats.n_cascade_added += 1
            stats.kernel_fired[k.name] = stats.kernel_fired.get(k.name, 0) + 1
            frontier.append((child, depth + 1))

    result.sort(key=lambda h: h.day)
    return result, stats
