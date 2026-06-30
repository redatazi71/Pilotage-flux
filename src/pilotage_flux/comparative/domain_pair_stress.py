"""Scénarios stress par paire de domaines MACRS.

Construit un scénario stress concentré sur 2 domaines doctrinaux de
la MACRS (demande, approvisionnement, logistique, production,
qualité). Permet d'étudier l'effet de la **coïncidence** de hazards
sur deux fronts différents.

Mapping doctrinal hazard → domaine (cf. matrice_incidence_causale.md) :
  - demande         → HAZARD_URGENT_ORDER  (R005 Avance commande)
  - approvisionnement → HAZARD_PO_DELAY    (R011 Retard livraison)
  - logistique      → HAZARD_LOGISTIC_DELAY (R019 Incident interne)
  - production      → HAZARD_BREAKDOWN     (R030 Panne machine)
  - qualite         → HAZARD_QUALITY_NC    (R039 NC produit)

Un scénario `pair_stress(D_a, D_b)` contient 12 hazards : 6 du
domaine `D_a` et 6 du domaine `D_b` (12 du même si D_a = D_b).
Les hazards sont distribués sur l'horizon avec jitter selon seed
pour produire de la variance inter-runs.
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


# Domaines MACRS doctrinaux + leur hazard canonique
DOMAINS = ("demande", "approvisionnement", "logistique",
            "production", "qualite")

DOMAIN_TO_HAZARD: dict[str, str] = {
    "demande":          HAZARD_URGENT_ORDER,
    "approvisionnement": HAZARD_PO_DELAY,
    "logistique":       HAZARD_LOGISTIC_DELAY,
    "production":       HAZARD_BREAKDOWN,
    "qualite":          HAZARD_QUALITY_NC,
}


def _make_hazard(
    kind: str, day: int, seed_rng: random.Random,
) -> HazardEvent:
    """Construit un HazardEvent réaliste pour un kind donné, avec
    jitter sur le payload."""
    if kind == HAZARD_BREAKDOWN:
        ws = seed_rng.choice(["WS-1", "WS-2", "WS-3"])
        return HazardEvent(
            day=day, kind=kind,
            payload={
                "workstation_id": ws,
                "duration_days": seed_rng.randint(1, 3),
                "slowdown_factor": round(
                    1.5 + seed_rng.random() * 0.8, 2,
                ),
            },
        )
    if kind == HAZARD_QUALITY_NC:
        return HazardEvent(
            day=day, kind=kind,
            payload={
                "article_id": "ART-A",
                "qty_scrap": seed_rng.randint(6, 14),
                "severity": "normal",
            },
        )
    if kind == HAZARD_PO_DELAY:
        po_id = seed_rng.choice(["PO-0001", "PO-0002"])
        return HazardEvent(
            day=day, kind=kind,
            payload={
                "po_id": po_id,
                "delay_days": seed_rng.randint(2, 5),
            },
        )
    if kind == HAZARD_URGENT_ORDER:
        return HazardEvent(
            day=day, kind=kind,
            payload={
                "sales_order_id": f"SO-URG-D{day}-r{seed_rng.randint(0, 999)}",
                "article_id": "ART-A",
                "quantity": seed_rng.randint(10, 25),
                "due_day": day + seed_rng.randint(2, 4),
            },
        )
    if kind == HAZARD_LOGISTIC_DELAY:
        ws = seed_rng.choice(["WS-1", "WS-2", "WS-3"])
        return HazardEvent(
            day=day, kind=kind,
            payload={
                "workstation_id": ws,
                "block_days": seed_rng.randint(1, 3),
            },
        )
    raise ValueError(f"kind inconnu : {kind}")


def pair_stress_scenario(
    domain_a: str,
    domain_b: str,
    *,
    seed: int = 42,
    horizon_days: int = 15,
    n_hazards_per_domain: int = 4,
    name: str | None = None,
) -> Scenario:
    """Construit un scénario stress concentré sur la paire de domaines.

    8 hazards par défaut (4+4), répartis aléatoirement sur l'horizon
    15j avec un espacement minimal de 1 jour pour éviter les
    chevauchements artificiels.

    Si `domain_a == domain_b`, on a 8 hazards du même domaine — utile
    pour mesurer la résilience à un stress mono-domaine.

    Volume calibré pour permettre la formation de contrats FLUX
    (horizon court + 8 hazards conserve la praticabilité opération-
    nelle même à saturation 0.86-0.94).
    """
    if domain_a not in DOMAINS:
        raise ValueError(
            f"domain_a inconnu : {domain_a} (attendu {DOMAINS})"
        )
    if domain_b not in DOMAINS:
        raise ValueError(
            f"domain_b inconnu : {domain_b} (attendu {DOMAINS})"
        )

    rng = random.Random(seed)
    kind_a = DOMAIN_TO_HAZARD[domain_a]
    kind_b = DOMAIN_TO_HAZARD[domain_b]

    # Génère les jours d'apparition : distribution uniforme entre
    # jours 1 et horizon-1 avec espacement >= 1 jour.
    n_total = n_hazards_per_domain * 2
    candidate_days = list(range(1, horizon_days - 1))
    rng.shuffle(candidate_days)
    days: list[int] = []
    min_spacing = max(1, (horizon_days - 2) // (n_total + 1))
    for d in candidate_days:
        if all(abs(d - d2) >= min_spacing for d2 in days):
            days.append(d)
        if len(days) == n_total:
            break
    if len(days) < n_total:
        # Si pas assez d'espace, on relâche la contrainte
        days = sorted(rng.sample(
            range(1, horizon_days - 1), min(n_total, horizon_days - 2),
        ))
    else:
        days.sort()

    hazards: list[HazardEvent] = []
    # Alternance : kind_a sur les jours pairs de la liste, kind_b sur
    # les jours impairs — produit une cascade entremêlée plus stressante.
    for i, day in enumerate(days):
        kind = kind_a if i % 2 == 0 else kind_b
        if domain_a == domain_b:
            kind = kind_a
        hazards.append(_make_hazard(kind, day, rng))

    base = baseline_scenario()
    return replace(
        base,
        name=name or f"pair_{domain_a}_{domain_b}_s{seed}",
        seed=seed,
        horizon_days=horizon_days,
        hazards=hazards,
    )


def all_pairs() -> list[tuple[str, str]]:
    """Renvoie les 25 paires ordonnées (D_a, D_b) — diagonale incluse."""
    return [(a, b) for a in DOMAINS for b in DOMAINS]
