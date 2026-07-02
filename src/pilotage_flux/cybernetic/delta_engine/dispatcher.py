"""V12.3 — Dispatcher Delta engine.

Reçoit une décision de tolérance V3 (`tolerance_filter_decisions`)
et la route vers l'un des 4 niveaux d'autonomie :

  L1 → traçage uniquement, pas d'action (déjà appliqué par V3)
  L2 → application autonome (V3 actionnel correct_local déjà géré)
  L3 → enqueue pour validation humaine, action en attente
  L4 → enqueue pour validation supervisor, action en attente

Le dispatcher est *idempotent* : appelé deux fois avec le même
decision_id il ne crée pas de doublon dans la queue.

Ext-h — Un `bounded_rationality_engine` optionnel peut être injecté
pour appliquer les biais Simon / Kahneman-Tversky / fatigue sur les
décisions L3/L4 avant enqueue. Sans engine, comportement V12.3 inchangé.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pilotage_flux.cybernetic.delta_engine.approval_queue import (
    STATUS_PENDING,
    submit_to_approval_queue,
)
from pilotage_flux.cybernetic.delta_engine.autonomy_levels import (
    AUTONOMY_LEVEL_L1,
    AUTONOMY_LEVEL_L2,
    AUTONOMY_LEVEL_L3,
    AUTONOMY_LEVEL_L4,
    REQUIRES_APPROVAL,
    classify_autonomy_level,
)


@dataclass(frozen=True)
class DispatchResult:
    decision_id: int
    autonomy_level: str
    requires_approval: bool
    queue_id: int | None
    immediately_actionable: bool


def dispatch_decision(
    conn: sqlite3.Connection,
    decision_id: int,
    *,
    action_level: str | None = None,
    bounded_rationality: "Any | None" = None,
    day_index: int = 0,
) -> DispatchResult:
    """Route une décision V3 vers son niveau d'autonomie V12.3.

    Parameters
    ----------
    decision_id : int
        ID dans `tolerance_filter_decisions`.
    action_level : str, optional
        Si fourni, court-circuite la lecture de la DB. Sinon, on lit
        l'action_level depuis tolerance_filter_decisions.
    bounded_rationality : BoundedRationalityEngine, optional
        Ext-h — si fourni, applique les biais humains sur la décision
        L3/L4 avant enqueue. Peut promouvoir vers L4 (fatigue) ou bloquer
        l'enqueue (aversion à la perte).
    day_index : int
        Jour simulé courant (nécessaire pour le compteur fatigue).

    Returns
    -------
    DispatchResult
        Avec autonomy_level, requires_approval, queue_id (None si L1/L2).
    """
    if action_level is None:
        row = conn.execute(
            "SELECT action_level, score_combined FROM tolerance_filter_decisions "
            "WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"decision_id inconnu : {decision_id}")
        action_level = row["action_level"]
        raw_score = float(row["score_combined"]) if row["score_combined"] is not None else 0.5
    else:
        raw_score = 0.5

    level = classify_autonomy_level(action_level)
    needs_approval = level in REQUIRES_APPROVAL

    # Ext-h — appliquer les biais humains si engine fourni
    if bounded_rationality is not None and needs_approval:
        from pilotage_flux.cybernetic.human_loop.bounded_rationality import (
            DecisionContext,
        )
        ctx = DecisionContext(
            raw_score=raw_score,
            threshold=0.5,
            estimated_cost_eur=0.0,
            day_index=day_index,
        )
        biased = bounded_rationality.evaluate(ctx)
        # Si fatigue active : promotion vers L4 (déchargement)
        if "fatigue" in biased.active_biases and level == AUTONOMY_LEVEL_L3:
            level = AUTONOMY_LEVEL_L4
        # Si aversion à la perte non-triggered : on n'escalade pas
        if "loss_aversion" in biased.active_biases and not biased.triggered:
            needs_approval = False

    queue_id: int | None = None
    if needs_approval:
        # Idempotence : si déjà en queue, on ne recrée pas
        existing = conn.execute(
            "SELECT queue_id FROM approval_queue WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()
        if existing is not None:
            queue_id = int(existing["queue_id"])
        else:
            queue_id = submit_to_approval_queue(
                conn, decision_id=decision_id, autonomy_level=level,
            )

    # L1/L2 sont immédiatement actionables (L1 = absorbé, L2 = déjà appliqué
    # par V3 actionnel). L3/L4 ne le sont qu'après approbation.
    immediately = level in {AUTONOMY_LEVEL_L1, AUTONOMY_LEVEL_L2}

    return DispatchResult(
        decision_id=decision_id,
        autonomy_level=level,
        requires_approval=needs_approval,
        queue_id=queue_id,
        immediately_actionable=immediately,
    )
