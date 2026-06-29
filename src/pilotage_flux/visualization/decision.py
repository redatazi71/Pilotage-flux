"""Flux décisionnel (L6.2 — famille 4/5).

Vue des décisions des portes (P1, P2, P3, P3 inverse, P3 collective, P4) +
transitions de zones + décisions du filtre dual de tolérances (V3).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass
class GateDecisionItem:
    gate: str
    subject_type: str
    subject_id: str
    decision: str
    cycle_id: str | None
    at_time: str
    explanation: str | None


@dataclass
class ZoneTransitionItem:
    subject_id: str
    from_zone: str | None
    to_zone: str
    decision: str | None
    at_time: str


@dataclass
class ToleranceActionItem:
    decision_id: int
    candidate_id: str | None
    action_level: str
    score_combined: float
    triggered_at: str | None


@dataclass
class DecisionFlowReport:
    gate_decisions: list[GateDecisionItem] = field(default_factory=list)
    zone_transitions: list[ZoneTransitionItem] = field(default_factory=list)
    tolerance_actions: list[ToleranceActionItem] = field(default_factory=list)

    @property
    def total_decisions(self) -> int:
        return (len(self.gate_decisions) + len(self.zone_transitions)
                + len(self.tolerance_actions))

    def actions_by_level(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for a in self.tolerance_actions:
            out.setdefault(a.action_level, 0)
            out[a.action_level] += 1
        return out


def decision_flow_view(conn: sqlite3.Connection) -> DecisionFlowReport:
    """Agrège l'historique de toutes les décisions du système."""
    report = DecisionFlowReport()

    # Décisions des portes (V0 gate_decisions + V1 gate_decisions_v1)
    for row in conn.execute(
        """
        SELECT gate, subject_type, subject_id, decision, NULL AS cycle_id,
               at_time, explanation
        FROM gate_decisions
        UNION ALL
        SELECT gate, subject_type, subject_id, decision, cycle_id,
               evaluated_at AS at_time, explanation
        FROM gate_decisions_v1
        ORDER BY at_time ASC
        """
    ).fetchall():
        report.gate_decisions.append(GateDecisionItem(
            gate=row["gate"],
            subject_type=row["subject_type"],
            subject_id=row["subject_id"],
            decision=row["decision"],
            cycle_id=row["cycle_id"],
            at_time=row["at_time"],
            explanation=row["explanation"],
        ))

    # Transitions de zones (V1)
    for row in conn.execute(
        """
        SELECT subject_id, from_zone, to_zone, decision, at_time
        FROM zone_transitions
        ORDER BY at_time ASC, transition_id ASC
        """
    ).fetchall():
        report.zone_transitions.append(ZoneTransitionItem(
            subject_id=row["subject_id"],
            from_zone=row["from_zone"],
            to_zone=row["to_zone"],
            decision=row["decision"],
            at_time=row["at_time"],
        ))

    # Filtre dual de tolérances (V3) — actions déclenchées
    for row in conn.execute(
        """
        SELECT decision_id, candidate_id, action_level, score_combined,
               triggered_at
        FROM tolerance_filter_decisions
        ORDER BY decision_id ASC
        """
    ).fetchall():
        report.tolerance_actions.append(ToleranceActionItem(
            decision_id=int(row["decision_id"]),
            candidate_id=row["candidate_id"],
            action_level=row["action_level"],
            score_combined=float(row["score_combined"]),
            triggered_at=row["triggered_at"],
        ))

    return report
