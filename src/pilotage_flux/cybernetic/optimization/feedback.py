"""V12.2.1 — Optimisation feedback-aware (boucle d'apprentissage).

Cette extension ajoute à V12.2 deux mécaniques d'apprentissage :

  1. **RejectionMemory** : mémorise les décisions historiques rejetées
     (via approval_queue.status='rejected') pour éviter de
     reproposer le même type de plan. La signature est
     {decision_id, autonomy_level, deviation_kind} — si une
     déviation de même nature a déjà fait l'objet d'un rejet,
     le plan correspondant est marqué comme « connu mauvais ».

  2. **FragilityWeights** : calcule un coefficient de fragilité par
     poste de travail à partir de l'historique d'aléas. Les postes
     fréquemment en panne (event_deviations time_delta avec
     score élevé) reçoivent un poids > 1.0 que le solveur peut
     intégrer dans son objectif pour les éviter.

Ces deux outils ne modifient pas le CP-SAT — ils fournissent des
ajustements **en amont** (RejectionMemory exclusion) ou
**dans l'objectif** (FragilityWeights pondération).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta


# ---------------------------------------------------------------------
# 1. RejectionMemory — mémoire des plans rejetés
# ---------------------------------------------------------------------

@dataclass
class RejectionRecord:
    """Une entrée d'approval_queue.status='rejected'."""

    queue_id: int
    decision_id: int
    autonomy_level: str
    approved_by: str | None
    notes: str | None
    deviation_kind: str | None
    score_combined: float | None
    rejected_at: str | None


class RejectionMemory:
    """Charge et expose les décisions historiques rejetées.

    Usage :

        memory = RejectionMemory(conn)
        # Avant de proposer un re-plan via V12.2 cp_sat_dynamic :
        if memory.is_decision_in_rejected_pattern(deviation_kind="time_delta", score=0.8):
            # → skip ou marque le plan comme « connu fragile »
            ...
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._records: list[RejectionRecord] = self._load()

    def _load(self) -> list[RejectionRecord]:
        rows = self._conn.execute(
            """
            SELECT
                aq.queue_id, aq.decision_id, aq.autonomy_level,
                aq.approved_by, aq.notes, aq.approved_at,
                tfd.score_combined,
                ed.deviation_kind
            FROM approval_queue aq
            LEFT JOIN tolerance_filter_decisions tfd
                   ON tfd.decision_id = aq.decision_id
            LEFT JOIN event_deviations ed
                   ON ed.deviation_id = tfd.deviation_id
            WHERE aq.status = 'rejected'
            """
        ).fetchall()
        out: list[RejectionRecord] = []
        for r in rows:
            out.append(RejectionRecord(
                queue_id=int(r["queue_id"]),
                decision_id=int(r["decision_id"]),
                autonomy_level=r["autonomy_level"],
                approved_by=r["approved_by"],
                notes=r["notes"],
                deviation_kind=r["deviation_kind"],
                score_combined=(
                    float(r["score_combined"])
                    if r["score_combined"] is not None else None
                ),
                rejected_at=r["approved_at"],
            ))
        return out

    @property
    def records(self) -> list[RejectionRecord]:
        return list(self._records)

    @property
    def n_records(self) -> int:
        return len(self._records)

    def is_decision_id_rejected(self, decision_id: int) -> bool:
        """True si une décision spécifique a déjà été rejetée."""
        return any(r.decision_id == decision_id for r in self._records)

    def is_known_bad_pattern(
        self,
        deviation_kind: str,
        score: float,
        autonomy_level: str | None = None,
        score_tolerance: float = 0.10,
    ) -> bool:
        """True si un pattern similaire a déjà été rejeté.

        On considère deux patterns similaires si :
          - même deviation_kind
          - score_combined dans ±score_tolerance
          - même autonomy_level (si fourni)
        """
        for r in self._records:
            if r.deviation_kind != deviation_kind:
                continue
            if r.score_combined is None:
                continue
            if abs(r.score_combined - score) > score_tolerance:
                continue
            if (
                autonomy_level is not None
                and r.autonomy_level != autonomy_level
            ):
                continue
            return True
        return False

    def rejection_rate_by_level(self) -> dict[str, float]:
        """Taux de rejet par autonomy_level (rejected / total processed)."""
        rows = self._conn.execute(
            """
            SELECT autonomy_level,
                   SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected,
                   SUM(CASE WHEN status IN ('approved', 'rejected') THEN 1 ELSE 0 END) AS total
            FROM approval_queue
            GROUP BY autonomy_level
            """
        ).fetchall()
        out: dict[str, float] = {}
        for r in rows:
            tot = int(r["total"]) if r["total"] else 0
            rej = int(r["rejected"]) if r["rejected"] else 0
            out[r["autonomy_level"]] = rej / tot if tot > 0 else 0.0
        return out


# ---------------------------------------------------------------------
# 2. FragilityWeights — pondération postes par fragilité historique
# ---------------------------------------------------------------------

@dataclass
class FragilityWeights:
    """Coefficients de fragilité par workstation_id.

    Construit à partir de l'historique d'écarts (event_deviations
    joints aux ordres via order_operations). Un poste sans écart →
    poids 1.0. Un poste avec beaucoup d'écarts → poids > 1.0
    (jusqu'à `max_weight`).

    Le poids est calculé comme :
        weight = 1.0 + alpha × log(1 + N_deviations_window) /
                       log(1 + max_N_in_db)
    avec alpha = max_weight - 1.0.
    """

    weights: dict[str, float] = field(default_factory=dict)
    window_days: int = 30
    max_weight: float = 2.0

    @classmethod
    def from_conn(
        cls,
        conn: sqlite3.Connection,
        *,
        window_days: int = 30,
        max_weight: float = 2.0,
        reference_date: datetime | None = None,
    ) -> "FragilityWeights":
        if reference_date is None:
            reference_date = datetime.utcnow()
        cutoff = (
            reference_date - timedelta(days=window_days)
        ).isoformat()

        # Compte les déviations par workstation_id, en joignant via
        # candidate_id → manufacturing_orders → order_operations
        rows = conn.execute(
            """
            SELECT oo.workstation_id, COUNT(DISTINCT ed.deviation_id) AS n
            FROM event_deviations ed
            JOIN candidate_orders c
              ON c.candidate_id = ed.candidate_id
            JOIN manufacturing_orders mo
              ON mo.candidate_id = c.candidate_id
            JOIN order_operations oo
              ON oo.of_id = mo.of_id
            WHERE ed.detected_at >= ?
            GROUP BY oo.workstation_id
            """,
            (cutoff,),
        ).fetchall()

        counts: dict[str, int] = {}
        for r in rows:
            counts[r["workstation_id"]] = int(r["n"])

        weights: dict[str, float] = {}
        if counts:
            import math
            max_n = max(counts.values())
            alpha = max_weight - 1.0
            log_max = math.log(1 + max_n) or 1.0
            for ws, n in counts.items():
                w = 1.0 + alpha * (math.log(1 + n) / log_max)
                weights[ws] = round(w, 4)

        return cls(
            weights=weights,
            window_days=window_days,
            max_weight=max_weight,
        )

    def get_weight(self, workstation_id: str) -> float:
        """Poids du poste, 1.0 si non observé (poste neuf ou fiable)."""
        return self.weights.get(workstation_id, 1.0)

    def is_fragile(
        self, workstation_id: str, threshold: float = 1.5,
    ) -> bool:
        """True si le poids dépasse le seuil de fragilité."""
        return self.get_weight(workstation_id) >= threshold

    def fragile_workstations(
        self, threshold: float = 1.5,
    ) -> list[str]:
        """Liste des postes considérés comme fragiles, du plus fragile
        au moins fragile."""
        return sorted(
            (ws for ws, w in self.weights.items() if w >= threshold),
            key=lambda ws: -self.weights[ws],
        )
