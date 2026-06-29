"""Flux événementiel (L6.2 — famille 5/5).

Vue des événements attendus / réels / écarts qualifiés (V3). Agrège
expected_events × event_deviations × event_deviation_causes pour donner
un panorama de l'écart entre la planification et la réalité.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass
class EventLine:
    candidate_id: str
    event_type: str
    expected_at: str
    matched: bool
    actual_at: str | None
    delta_minutes: float | None
    qualification: str | None
    is_absorbed: bool
    cause_label: str | None


@dataclass
class EventFlowReport:
    lines: list[EventLine] = field(default_factory=list)

    @property
    def total_expected(self) -> int:
        return len(self.lines)

    @property
    def total_matched(self) -> int:
        return sum(1 for l in self.lines if l.matched)

    @property
    def total_critical(self) -> int:
        return sum(1 for l in self.lines if l.qualification == "critical")

    @property
    def match_rate(self) -> float:
        if not self.lines:
            return 0.0
        return self.total_matched / len(self.lines)


def event_flow_view(
    conn: sqlite3.Connection, batch_id: str | None = None
) -> EventFlowReport:
    """Vue ligne par ligne : événement attendu, son match réel, son écart,
    sa qualification, sa cause principale (la plus haute pondération).

    Si batch_id est fourni, restreint à cette tranche gelée.
    """
    sql = """
        SELECT ee.candidate_id, ee.event_type, ee.expected_at,
               ee.matched_actual_id, ee.matched_at,
               ed.delta_value, ed.qualification, ed.is_absorbed,
               ed.deviation_id,
               es.occurred_at AS actual_at
        FROM expected_events ee
        LEFT JOIN event_deviations ed ON ed.expected_event_id = ee.expected_event_id
        LEFT JOIN event_store es ON es.event_id = ee.matched_actual_id
    """
    params: list[str] = []
    if batch_id is not None:
        sql += " WHERE ee.batch_id = ?"
        params.append(batch_id)
    sql += " ORDER BY ee.candidate_id ASC, ee.expected_at ASC, ee.expected_event_id ASC"

    report = EventFlowReport()
    for row in conn.execute(sql, params).fetchall():
        cause_label: str | None = None
        if row["deviation_id"] is not None:
            cause_row = conn.execute(
                """
                SELECT rc.label, edc.score
                FROM event_deviation_causes edc
                JOIN root_cause_rules rc
                  ON rc.rule_id = edc.rule_id AND rc.version = edc.rule_version
                WHERE edc.deviation_id = ?
                ORDER BY edc.score DESC LIMIT 1
                """,
                (int(row["deviation_id"]),),
            ).fetchone()
            if cause_row:
                cause_label = cause_row["label"]
        report.lines.append(EventLine(
            candidate_id=row["candidate_id"],
            event_type=row["event_type"],
            expected_at=row["expected_at"],
            matched=row["matched_actual_id"] is not None,
            actual_at=row["actual_at"],
            delta_minutes=(
                float(row["delta_value"])
                if row["delta_value"] is not None else None
            ),
            qualification=row["qualification"],
            is_absorbed=bool(row["is_absorbed"]) if row["is_absorbed"] is not None else False,
            cause_label=cause_label,
        ))
    return report
