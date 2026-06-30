"""Wiring MACRS Couche 2 → décision niveau Delta — B.3.

Chaîne complète de la couche cybernétique :

    déviation observée
        ↓
    attribution causale (racine_id, categorie_code)
        ↓
    MACRS Couche 2 .record_event(racine, categorie, occurred_at,
                                  delay_hours, impact_score)
        ↓
    filtre dual tolérances (B.2) → niveau de base L1..L6
        ↓
    boost MACRS selon état de la cellule :
        - cellule ACTIVE + ratio_emergence ≥ 1.5  → +1 niveau
        - cellule ACTIVE + ratio_emergence ≥ 3.0  → +2 niveaux
        - cellule ACTIVE + criticité élevée       → +1 niveau
    (cap final L6)
        ↓
    delta_decision créée + (si requires_human) enqueue
    approval_queue → retour DeltaDecision finale.

Le boost MACRS reflète la doctrine cybernétique : une racine en
émergence ou de criticité forte mérite une réaction plus marquée
que ce que dirait le filtre dual seul.

Paramètres exposés via `parameters` :
  - macrs_boost_ratio_low    (default 1.5)
  - macrs_boost_ratio_high   (default 3.0)
  - macrs_boost_criticite    (default 0.20 — criticité/jour)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pilotage_flux.cybernetic.delta_engine.approval_queue import (
    submit_to_approval_queue,
)
from pilotage_flux.cybernetic.delta_engine.autonomy_levels import (
    AUTONOMY_LEVEL_L2,
    AUTONOMY_LEVEL_L3,
    AUTONOMY_LEVEL_L4,
)
from pilotage_flux.cybernetic.delta_engine.decisions import (
    DeltaDecision,
    get_decision,
)
from pilotage_flux.cybernetic.delta_engine.levels import (
    NIVEAUX_ORDRE,
    get_delta_level,
)
from pilotage_flux.cybernetic.delta_engine.tolerance_filter import (
    evaluate_and_decide,
)
from pilotage_flux.cybernetic.macrs.couche2 import (
    aggregate_cell_by_couple,
    record_event as macrs_record_event,
)
from pilotage_flux.parameters import get_num


DEFAULT_BOOST_RATIO_LOW = 1.5
DEFAULT_BOOST_RATIO_HIGH = 3.0
DEFAULT_BOOST_CRITICITE = 0.20


# Mapping niveau_code L1..L6 → autonomy_level (V12.3 / approval_queue).
# Seuls les niveaux requires_human enqueueront.
NIVEAU_TO_AUTONOMY: dict[str, str] = {
    "L3": AUTONOMY_LEVEL_L2,    # corriger_local : auto-adjust scope local
    "L4": AUTONOMY_LEVEL_L3,    # replanifier_local : local approval
    "L5": AUTONOMY_LEVEL_L3,    # escalader : local approval (transition)
    "L6": AUTONOMY_LEVEL_L4,    # replanifier_global : global approval
}


@dataclass(frozen=True)
class CyberneticDecisionResult:
    """Résultat complet de la chaîne de décision cybernétique."""
    delta_decision: DeltaDecision
    base_niveau: str                # ce que disait le filtre dual seul
    final_niveau: str               # après boost MACRS
    boost_applied: int               # 0, 1 ou 2
    boost_reason: str | None         # 'emerging_low' | 'emerging_high' |
                                      # 'critical' | None
    approval_queue_id: int | None    # si enqueue


def _boost_from_macrs_signals(
    conn: sqlite3.Connection,
    racine_id: str,
    categorie_code: str,
    now_iso: str,
) -> tuple[int, str | None]:
    """Calcule le boost de niveau à partir des signaux Couche 2.

    Renvoie (boost, reason). boost = 0 si pas de cellule ou cellule
    inactive (INCOMING / OBSERVING), ou si signaux sous les seuils.
    """
    try:
        agg = aggregate_cell_by_couple(
            conn, racine_id, categorie_code, now_iso=now_iso,
        )
    except ValueError:
        return 0, None
    if agg.status != "ACTIVE":
        return 0, None

    ratio_low = _get_param(
        conn, "macrs_boost_ratio_low", DEFAULT_BOOST_RATIO_LOW,
    )
    ratio_high = _get_param(
        conn, "macrs_boost_ratio_high", DEFAULT_BOOST_RATIO_HIGH,
    )
    crit_threshold = _get_param(
        conn, "macrs_boost_criticite", DEFAULT_BOOST_CRITICITE,
    )

    ratio = agg.ratio_emergence
    if ratio is not None:
        if ratio >= ratio_high:
            return 2, "emerging_high"
        if ratio >= ratio_low:
            return 1, "emerging_low"

    # Criticité approximative : n_w_courte / 30
    criticite = agg.n_w_courte / 30.0
    if criticite >= crit_threshold:
        return 1, "critical"
    return 0, None


def _get_param(
    conn: sqlite3.Connection, name: str, default: float,
) -> float:
    v = get_num(
        conn, scope="global", scope_ref=None,
        name=name, default=default,
    )
    return float(v) if v is not None else default


def _escalate_niveau(niveau: str, boost: int) -> str:
    """Avance le niveau dans l'ordre canonique, cap à L6."""
    if boost <= 0:
        return niveau
    try:
        idx = NIVEAUX_ORDRE.index(niveau)
    except ValueError:
        return niveau
    new_idx = min(idx + boost, len(NIVEAUX_ORDRE) - 1)
    return NIVEAUX_ORDRE[new_idx]


def record_and_decide(
    conn: sqlite3.Connection,
    *,
    deviation_id: int,
    racine_id: str,
    categorie_code: str,
    occurred_at: str,
    decided_at: str,
    delay_hours: float | None = None,
    impact_score: float | None = None,
    enqueue_if_human: bool = True,
    actor: str = "auto:delta_engine",
) -> CyberneticDecisionResult:
    """Chaîne complète déviation → MACRS → filtre dual → delta_decision.

    Paramètres :
      deviation_id    : déviation à traiter (event_deviations.deviation_id)
      racine_id       : R001..R046 — attribution causale
      categorie_code  : Mat | Cap | Op | Qual | Temp | Info | Sync
      occurred_at     : horodatage de l'événement causal (ISO)
      decided_at      : horodatage de la décision (ISO)
      delay_hours     : optionnel, alimente l'histogramme délai MACRS
      impact_score    : optionnel, alimente l'impact pondéré MACRS
      enqueue_if_human: si True (default), enqueue dans approval_queue
                        pour les niveaux L4/L5/L6 ; si False, le caller
                        gère lui-même la subsidiarité humaine

    Renvoie un CyberneticDecisionResult avec :
      - delta_decision : la décision finale (status='pending')
      - base_niveau    : niveau avant boost
      - final_niveau   : niveau après boost MACRS
      - boost_applied  : 0, 1 ou 2
      - boost_reason   : raison du boost
      - approval_queue_id : si enqueue effectué
    """
    # 1. Alimente MACRS Couche 2 (synchrone, §6 spec)
    macrs_record_event(
        conn, racine_id, categorie_code,
        occurred_at=occurred_at,
        delay_hours=delay_hours,
        impact_score=impact_score,
    )

    # 2. Filtre dual + delta_decision de base (B.2)
    tol, delta_id = evaluate_and_decide(
        conn, deviation_id,
        decided_at=decided_at,
        racine_id=racine_id,
        categorie_code=categorie_code,
        actor=actor,
    )
    base = get_decision(conn, delta_id)
    assert base is not None
    base_niveau = base.niveau_code

    # 3. Boost MACRS selon état de la cellule
    boost, reason = _boost_from_macrs_signals(
        conn, racine_id, categorie_code, now_iso=decided_at,
    )
    final_niveau = _escalate_niveau(base_niveau, boost)

    # 4. Si boost effectif, met à jour la delta_decision
    if final_niveau != base_niveau:
        explanation = (
            base.explanation or ""
        ) + f" | MACRS boost +{boost} ({reason}): {base_niveau}→{final_niveau}"
        conn.execute(
            "UPDATE delta_decisions "
            "SET niveau_code = ?, explanation = ? "
            "WHERE delta_decision_id = ?",
            (final_niveau, explanation, delta_id),
        )

    # 5. Enqueue dans approval_queue si requires_human
    approval_queue_id: int | None = None
    lvl = get_delta_level(conn, final_niveau)
    if lvl is not None and lvl.requires_human and enqueue_if_human:
        autonomy = NIVEAU_TO_AUTONOMY.get(final_niveau)
        if autonomy is not None:
            approval_queue_id = submit_to_approval_queue(
                conn, tol.decision_id, autonomy,
                notes=(
                    f"racine={racine_id}, cat={categorie_code}, "
                    f"niveau={final_niveau}"
                ),
            )
            conn.execute(
                "UPDATE delta_decisions SET approval_queue_id = ? "
                "WHERE delta_decision_id = ?",
                (approval_queue_id, delta_id),
            )

    final = get_decision(conn, delta_id)
    assert final is not None
    return CyberneticDecisionResult(
        delta_decision=final,
        base_niveau=base_niveau,
        final_niveau=final.niveau_code,
        boost_applied=boost,
        boost_reason=reason,
        approval_queue_id=approval_queue_id,
    )
