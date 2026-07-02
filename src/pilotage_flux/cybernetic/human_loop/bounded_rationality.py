"""Ext-h — Facteur humain : modèle de rationalité bornée.

Modélise l'écart entre le décideur idéal (agent parfaitement rationnel)
et le décideur humain réel, qui subit :

1. **Bruit décisionnel** (Simon 1955 — satisficing) : le seuil de décision
   est perturbé par un bruit gaussien de σ configurable. Un score
   légèrement au-dessus du seuil peut être manqué, ou l'inverse.

2. **Ancrage** (Tversky-Kahneman 1974) : les décisions récentes biaisent
   la décision courante. On agrège sur une fenêtre glissante avec un
   poids exponentiel décroissant `anchoring_decay`. Si le décideur a
   récemment escaladé plusieurs cas, il aura tendance à ré-escalader.

3. **Aversion à la perte** (Kahneman-Tversky 1979, λ ≈ 2.25) : un replan
   coûteux est rejeté 2.25× plus souvent qu'un gain équivalent n'est
   accepté. Modélisé comme un facteur multiplicatif sur les décisions
   dont le coût attendu > 0.

4. **Fatigue** (Danziger et al. 2011 — parole board studies) : après
   N décisions dans la même journée simulée, le seuil s'assouplit
   (fatigue → escalation plus fréquente vers L3/L4 pour se décharger).

Ce module ne modifie **pas** les décisions du filtre dual V3. Il wrappe
la couche `dispatch_decision` / `approve_decision` pour introduire les
biais quand `enable_bounded_rationality=True`.

Le paper montre alors que **la couche événementielle absorbe le bruit
humain** (la mémoire causale rectifie les décisions incohérentes) alors
qu'un pilotage OF classique le propage sans filtre.
"""

from __future__ import annotations

import math
import random as _random
from dataclasses import dataclass, field
from typing import Any


@dataclass
class HumanDecisionModel:
    """Paramètres du décideur humain borné.

    Toutes les valeurs par défaut correspondent à un décideur *moyennement
    biaisé* — ni idéal, ni caricatural. Ce sont des ordres de grandeur
    calibrés depuis la littérature psychologie de la décision, pas des
    mesures propres à l'industrie.
    """

    # Bruit décisionnel — écart-type relatif au seuil de comparaison
    noise_std: float = 0.15
    """Écart-type du bruit gaussien appliqué au score de comparaison."""

    # Ancrage — poids des N décisions précédentes
    anchoring_window: int = 5
    """Nombre de décisions récentes considérées pour l'ancrage."""
    anchoring_decay: float = 0.6
    """Poids exponentiel — 1.0 = pas de décroissance, 0.0 = pas d'ancrage."""
    anchoring_strength: float = 0.2
    """Amplitude du biais d'ancrage vers la classe dominante récente."""

    # Aversion à la perte — Kahneman-Tversky λ
    loss_aversion_lambda: float = 2.25
    """Ratio d'aversion à la perte."""
    loss_threshold_eur: float = 200.0
    """Coût au-delà duquel l'aversion à la perte s'active."""

    # Fatigue — dégradation du jugement dans la journée
    fatigue_decisions_per_day: int = 25
    """Nombre de décisions avant que la fatigue commence à agir."""
    fatigue_slope: float = 0.03
    """Amplitude par décision au-delà du seuil (assouplit vers escalation)."""

    seed: int = 424242


@dataclass
class DecisionContext:
    """Contexte transmis au modèle à chaque décision.

    - `raw_score` : score combiné brut du filtre dual (0..1 typique).
    - `threshold` : seuil au-dessus duquel l'action est déclenchée.
    - `estimated_cost_eur` : coût économique attendu de l'action (0 si N/A).
    - `day_index` : jour simulé courant (pour tracker la fatigue).
    """

    raw_score: float
    threshold: float
    estimated_cost_eur: float = 0.0
    day_index: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BiasedDecision:
    """Résultat de la décision biaisée + traçabilité des biais actifs."""

    triggered: bool
    biased_score: float
    threshold_effective: float
    active_biases: list[str] = field(default_factory=list)


class BoundedRationalityEngine:
    """État persistant du décideur humain — nécessaire pour l'ancrage
    et la fatigue qui dépendent de l'historique."""

    def __init__(self, model: HumanDecisionModel) -> None:
        self.model = model
        self._rng = _random.Random(model.seed)
        self._recent_triggers: list[bool] = []
        self._day_decision_count: dict[int, int] = {}

    def _apply_noise(self, score: float) -> float:
        """Bruit décisionnel Simon 1955 (satisficing)."""
        if self.model.noise_std <= 0:
            return score
        return score + self._rng.gauss(0.0, self.model.noise_std)

    def _apply_anchoring(self, score: float) -> tuple[float, bool]:
        """Ancrage sur les décisions récentes."""
        recent = self._recent_triggers[-self.model.anchoring_window:]
        if not recent or self.model.anchoring_strength <= 0:
            return score, False
        # Poids exponentiel décroissant : la plus récente = poids max
        weights = [
            self.model.anchoring_decay ** i
            for i in range(len(recent) - 1, -1, -1)
        ]
        total_w = sum(weights) or 1.0
        # +1 si triggered, -1 sinon — moyenne pondérée vers la classe dominante
        signed = [(1.0 if t else -1.0) for t in recent]
        anchor_bias = (
            sum(w * s for w, s in zip(weights, signed)) / total_w
        ) * self.model.anchoring_strength
        return score + anchor_bias, abs(anchor_bias) > 0.01

    def _apply_loss_aversion(
        self, threshold: float, cost: float
    ) -> tuple[float, bool]:
        """Aversion à la perte (Kahneman-Tversky λ)."""
        if cost <= self.model.loss_threshold_eur:
            return threshold, False
        # Coût au-dessus du seuil → threshold plus haut (rejet plus facile)
        excess_ratio = min(
            2.0, (cost - self.model.loss_threshold_eur)
            / max(1.0, self.model.loss_threshold_eur)
        )
        factor = 1.0 + math.log1p(excess_ratio) * (
            self.model.loss_aversion_lambda - 1.0
        )
        return threshold * factor, True

    def _apply_fatigue(
        self, threshold: float, day_index: int
    ) -> tuple[float, bool]:
        """Fatigue — décale le seuil vers l'assouplissement (tendance à
        escalader plus rapidement en fin de journée)."""
        n_today = self._day_decision_count.get(day_index, 0)
        if n_today <= self.model.fatigue_decisions_per_day:
            return threshold, False
        excess = n_today - self.model.fatigue_decisions_per_day
        # Seuil s'assouplit → plus de triggers = plus d'escalations
        return max(0.0, threshold - excess * self.model.fatigue_slope), True

    def evaluate(self, ctx: DecisionContext) -> BiasedDecision:
        """Applique les 4 biais et retourne la décision perturbée."""
        active: list[str] = []
        score = ctx.raw_score

        score = self._apply_noise(score)
        if abs(score - ctx.raw_score) > 1e-6:
            active.append("noise")

        score, has_anchor = self._apply_anchoring(score)
        if has_anchor:
            active.append("anchoring")

        threshold = ctx.threshold
        threshold, has_loss = self._apply_loss_aversion(
            threshold, ctx.estimated_cost_eur
        )
        if has_loss:
            active.append("loss_aversion")

        # Fatigue s'applique après compteur mis à jour
        self._day_decision_count[ctx.day_index] = (
            self._day_decision_count.get(ctx.day_index, 0) + 1
        )
        threshold, has_fatigue = self._apply_fatigue(
            threshold, ctx.day_index
        )
        if has_fatigue:
            active.append("fatigue")

        triggered = score >= threshold
        self._recent_triggers.append(triggered)
        # Fenêtre bornée pour éviter croissance mémoire
        if len(self._recent_triggers) > self.model.anchoring_window * 4:
            self._recent_triggers = self._recent_triggers[
                -self.model.anchoring_window * 2:
            ]
        return BiasedDecision(
            triggered=triggered,
            biased_score=round(score, 4),
            threshold_effective=round(threshold, 4),
            active_biases=active,
        )
