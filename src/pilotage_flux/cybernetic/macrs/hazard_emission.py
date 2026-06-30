"""Couplage hazard → event_deviation → MACRS cell update — C.2.

Termine la chaîne d'étiquetage causal : quand un hazard du
scénario se manifeste durant le run, on enregistre une déviation
événementielle, on alimente la cellule MACRS correspondante, et
on déclenche la chaîne décisionnelle (filtre dual → niveau Delta
→ approval).

Le couplage est **non-invasif** pour les pilotages OF / FLUX sans
BCE : ces pilotages n'invoquent simplement pas `emit_hazard`, le
hazard reste géré par la mécanique historique de `runner.py`.

Pour le pilotage BCE, `emit_hazard` est appelé sur chaque hazard
au moment de sa manifestation : il (i) crée une `event_deviation`
visible par tout le V3, (ii) alimente la Couche 2 MACRS, (iii) crée
la `delta_decision` au bon niveau (avec boost MACRS si la racine
est en émergence ou critique), (iv) enqueue dans `approval_queue`
si requires_human.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pilotage_flux.comparative.scenario import (
    HAZARD_BREAKDOWN,
    HAZARD_LOGISTIC_DELAY,
    HAZARD_PO_DELAY,
    HAZARD_QUALITY_NC,
    HAZARD_URGENT_ORDER,
    HazardEvent,
)
from pilotage_flux.cybernetic.macrs.hazard_labels import resolve_racine

# Import paresseux (déféré) pour casser le cycle macrs.__init__ →
# hazard_emission → delta_engine.macrs_wiring → macrs.couche2.
if False:  # TYPE_CHECKING (sans introduire la dépendance runtime)
    from pilotage_flux.cybernetic.delta_engine.macrs_wiring import (
        CyberneticDecisionResult,
    )


# Type de déviation event_deviations.deviation_kind par défaut.
HAZARD_TO_DEVIATION_KIND: dict[str, str] = {
    HAZARD_BREAKDOWN:       "time_delta",
    HAZARD_QUALITY_NC:      "qty_delta",
    HAZARD_PO_DELAY:        "time_delta",
    HAZARD_URGENT_ORDER:    "time_delta",
    HAZARD_LOGISTIC_DELAY:  "time_delta",
}

# Impact pondéré par défaut (0..1). Le BCE peut surcharger via
# le payload de l'événement.
HAZARD_DEFAULT_IMPACT: dict[str, float] = {
    HAZARD_BREAKDOWN:       0.80,
    HAZARD_QUALITY_NC:      0.60,
    HAZARD_PO_DELAY:        0.50,
    HAZARD_URGENT_ORDER:    0.40,
    HAZARD_LOGISTIC_DELAY:  0.30,
}

# Délai de manifestation par défaut (heures). Cas typiques cadrage
# §2.4 : panne 8h, retard PO 48h.
HAZARD_DEFAULT_DELAY_HOURS: dict[str, float] = {
    HAZARD_BREAKDOWN:       8.0,
    HAZARD_QUALITY_NC:      24.0,
    HAZARD_PO_DELAY:        48.0,
    HAZARD_URGENT_ORDER:    2.0,
    HAZARD_LOGISTIC_DELAY:  4.0,
}


@dataclass(frozen=True)
class HazardEmissionResult:
    """Résultat de la propagation d'un hazard à travers la boucle."""
    hazard: HazardEvent
    racine_id: str | None
    categorie_code: str | None
    deviation_id: int | None
    cybernetic_decision: object | None     # CyberneticDecisionResult
    skipped_reason: str | None        # 'unknown_racine' | 'unmapped_categorie'


def _create_deviation_row(
    conn: sqlite3.Connection,
    *,
    deviation_kind: str,
    delta_value: float,
    score_magnitude: float,
    detected_at: str,
    qualification: str = "auto",
    candidate_id: str | None = None,
    is_absorbed: bool = False,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO event_deviations
            (deviation_kind, delta_value, score, qualification,
             detected_at, candidate_id, is_absorbed)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            deviation_kind, delta_value, score_magnitude,
            qualification, detected_at,
            candidate_id, 1 if is_absorbed else 0,
        ),
    )
    return int(cur.lastrowid)


def _extract_score(
    hazard: HazardEvent, default_impact: float,
) -> tuple[float, float]:
    """Lit (delta_value, score) depuis le payload, fallback sur les
    valeurs par défaut.

    delta_value : valeur brute de l'écart (minutes, qty…) — payload
                  'delta_value' ou 'delta_days' × 1440 ou 1.0.
    score       : magnitude normalisée 0..1 — payload 'impact_score'
                  ou HAZARD_DEFAULT_IMPACT.
    """
    payload = hazard.payload or {}
    score = float(payload.get("impact_score", default_impact))
    score = max(0.0, min(1.0, score))   # clamp 0..1

    # delta_value : si non fourni explicitement, dérive d'autres
    # champs du payload (delay_days, qty_lost…) ou 1.0 sentinelle.
    if "delta_value" in payload:
        delta_value = float(payload["delta_value"])
    elif "delay_days" in payload:
        delta_value = float(payload["delay_days"]) * 1440.0
    elif "qty_lost" in payload:
        delta_value = float(payload["qty_lost"])
    else:
        delta_value = 1.0
    return delta_value, score


def emit_hazard(
    conn: sqlite3.Connection,
    hazard: HazardEvent,
    *,
    occurred_at: str,
    decided_at: str,
    candidate_id: str | None = None,
    enqueue_if_human: bool = True,
    actor: str = "auto:bce_loop",
) -> HazardEmissionResult:
    """Propage un hazard à travers la boucle cybernétique.

    1. Résout (racine_id, categorie_code) via hazard_labels
    2. Crée un event_deviation row
    3. Appelle record_and_decide (B.3) qui chaîne MACRS Couche 2 +
       filtre dual + delta_decision + (optionnel) approval_queue.

    Skip silencieux (skipped_reason renseigné) si :
      - kind du hazard non reconnu par hazard_labels (racine ou
        catégorie introuvable)
    Cas où le caller doit traiter le hazard via la mécanique
    historique (runner.py legacy).
    """
    racine_id, categorie_code = resolve_racine(hazard, conn=conn)
    if racine_id is None:
        return HazardEmissionResult(
            hazard=hazard, racine_id=None, categorie_code=None,
            deviation_id=None, cybernetic_decision=None,
            skipped_reason="unknown_racine",
        )
    if categorie_code is None:
        return HazardEmissionResult(
            hazard=hazard, racine_id=racine_id, categorie_code=None,
            deviation_id=None, cybernetic_decision=None,
            skipped_reason="unmapped_categorie",
        )

    default_impact = HAZARD_DEFAULT_IMPACT.get(hazard.kind, 0.5)
    delta_value, score_magnitude = _extract_score(hazard, default_impact)
    deviation_kind = HAZARD_TO_DEVIATION_KIND.get(
        hazard.kind, "time_delta",
    )
    delay_hours = float(
        (hazard.payload or {}).get(
            "delay_hours",
            HAZARD_DEFAULT_DELAY_HOURS.get(hazard.kind, 4.0),
        )
    )

    dev_id = _create_deviation_row(
        conn,
        deviation_kind=deviation_kind,
        delta_value=delta_value,
        score_magnitude=score_magnitude,
        detected_at=occurred_at,
        qualification="auto",
        candidate_id=candidate_id,
    )

    # Import paresseux pour casser le cycle d'import
    from pilotage_flux.cybernetic.delta_engine.macrs_wiring import (
        record_and_decide,
    )
    cyber = record_and_decide(
        conn,
        deviation_id=dev_id,
        racine_id=racine_id,
        categorie_code=categorie_code,
        occurred_at=occurred_at,
        decided_at=decided_at,
        delay_hours=delay_hours,
        impact_score=score_magnitude,
        enqueue_if_human=enqueue_if_human,
        actor=actor,
    )

    return HazardEmissionResult(
        hazard=hazard,
        racine_id=racine_id,
        categorie_code=categorie_code,
        deviation_id=dev_id,
        cybernetic_decision=cyber,
        skipped_reason=None,
    )


def emit_hazards_batch(
    conn: sqlite3.Connection,
    hazards: list[HazardEvent],
    *,
    horizon_start_iso: str,
    enqueue_if_human: bool = True,
    actor: str = "auto:bce_loop",
) -> list[HazardEmissionResult]:
    """Propage une liste de hazards (typiquement le scenario.hazards).

    Pour chaque hazard, calcule occurred_at = horizon_start + day
    jours. decided_at = occurred_at + 1 minute (instantané).
    """
    from datetime import datetime, timedelta
    start = datetime.fromisoformat(horizon_start_iso)
    results: list[HazardEmissionResult] = []
    for h in hazards:
        occurred = start + timedelta(days=int(h.day))
        decided = occurred + timedelta(minutes=1)
        results.append(emit_hazard(
            conn, h,
            occurred_at=occurred.isoformat(),
            decided_at=decided.isoformat(),
            enqueue_if_human=enqueue_if_human,
            actor=actor,
        ))
    return results
