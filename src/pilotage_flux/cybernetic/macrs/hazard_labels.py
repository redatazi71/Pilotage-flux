"""Étiquetage causal des hazards — C.1.

Mapping par défaut entre les 5 hazards historiques du banc
comparatif et les couples (racine MACRS, catégorie Δ) qui leur
correspondent doctrinalement.

Permet à `record_and_decide` (B.3) de retrouver la cellule
opérationnelle à alimenter quand un hazard se manifeste, sans
modifier les scénarios existants — la résolution est rétro-
compatible.

Mapping canonique :

  breakdown_ws   → R030 Panne machine                (Op)
  quality_nc     → R039 NC produit interne           (Qual)
  po_delay       → R011 Retard livraison fournisseur (Mat)
  urgent_order   → R005 Avance de commande           (Temp)
  logistic_delay → R019 Incident transport interne   (Op)

Les couples sont **paramétrables** : si un test ou un scénario
souhaite réattribuer un hazard à une autre racine (par exemple
quality_nc → R012 NC fournisseur), il peut soit poser
l'étiquette directement dans HazardEvent.racine_id, soit
modifier la table `parameters` (scope='global') avec les noms
canoniques `hazard_racine_<kind>` et `hazard_categorie_<kind>`.
"""

from __future__ import annotations

import sqlite3

from pilotage_flux.comparative.scenario import (
    HAZARD_BREAKDOWN,
    HAZARD_LOGISTIC_DELAY,
    HAZARD_PO_DELAY,
    HAZARD_QUALITY_NC,
    HAZARD_URGENT_ORDER,
    HazardEvent,
)
from pilotage_flux.parameters import get_text


# Mapping canonique hazard → (racine_id, categorie_code) MACRS
HAZARD_TO_RACINE: dict[str, str] = {
    HAZARD_BREAKDOWN:       "R030",   # Panne machine
    HAZARD_QUALITY_NC:      "R039",   # NC produit interne
    HAZARD_PO_DELAY:        "R011",   # Retard livraison fournisseur
    HAZARD_URGENT_ORDER:    "R005",   # Avance de commande
    HAZARD_LOGISTIC_DELAY:  "R019",   # Incident transport interne
}

HAZARD_TO_CATEGORIE: dict[str, str] = {
    HAZARD_BREAKDOWN:       "Op",
    HAZARD_QUALITY_NC:      "Qual",
    HAZARD_PO_DELAY:        "Mat",
    HAZARD_URGENT_ORDER:    "Temp",
    HAZARD_LOGISTIC_DELAY:  "Op",
}


def default_racine_for(hazard_kind: str) -> str | None:
    """Racine MACRS par défaut pour un kind de hazard."""
    return HAZARD_TO_RACINE.get(hazard_kind)


def default_categorie_for(hazard_kind: str) -> str | None:
    """Catégorie Δ par défaut pour un kind de hazard."""
    return HAZARD_TO_CATEGORIE.get(hazard_kind)


def resolve_racine(
    hazard: HazardEvent,
    *,
    conn: sqlite3.Connection | None = None,
) -> tuple[str | None, str | None]:
    """Résout (racine_id, categorie_code) pour un HazardEvent.

    Ordre de priorité :
      1. Champs explicites de l'événement (hazard.racine_id,
         hazard.categorie_code) s'ils sont fournis ;
      2. Override via `parameters` (scope='global', noms
         `hazard_racine_<kind>` et `hazard_categorie_<kind>`) si
         `conn` est fourni ;
      3. Mapping canonique HAZARD_TO_RACINE / HAZARD_TO_CATEGORIE.

    Renvoie (None, None) si le kind est inconnu et non paramétré.
    """
    if hazard.racine_id is not None and hazard.categorie_code is not None:
        return hazard.racine_id, hazard.categorie_code

    racine = hazard.racine_id
    categorie = hazard.categorie_code

    if conn is not None:
        if racine is None:
            v = get_text(
                conn, scope="global", scope_ref=None,
                name=f"hazard_racine_{hazard.kind}", default=None,
            )
            racine = v if v else None
        if categorie is None:
            v = get_text(
                conn, scope="global", scope_ref=None,
                name=f"hazard_categorie_{hazard.kind}", default=None,
            )
            categorie = v if v else None

    if racine is None:
        racine = default_racine_for(hazard.kind)
    if categorie is None:
        categorie = default_categorie_for(hazard.kind)

    return racine, categorie


def labeled_hazard(
    day: int,
    kind: str,
    payload: dict,
    *,
    racine_id: str | None = None,
    categorie_code: str | None = None,
) -> HazardEvent:
    """Factory d'un HazardEvent avec étiquettes auto-résolues.

    Si racine_id / categorie_code ne sont pas fournis, applique le
    mapping canonique (HAZARD_TO_RACINE / HAZARD_TO_CATEGORIE).
    """
    return HazardEvent(
        day=day,
        kind=kind,
        payload=payload,
        racine_id=(
            racine_id if racine_id is not None
            else default_racine_for(kind)
        ),
        categorie_code=(
            categorie_code if categorie_code is not None
            else default_categorie_for(kind)
        ),
    )
