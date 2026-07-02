"""Ext-n — Trace causale end-to-end pour explainability.

Exporte la chaîne causale complète d'un run :

    déviation → cause hypothèse (R-RC-XX) → décision filtre dual
              → escalade éventuelle (approval_queue) → outcome

Chaque ligne de trace répond à la question du régulateur :
« pourquoi le système a-t-il pris cette action à ce moment-là ? ».

Cette trace est le socle de la conformité AI Act / IA de confiance :
elle rend chaque décision auditable et contestable a posteriori.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CausalTraceRow:
    deviation_id: int
    candidate_id: str | None
    deviation_kind: str
    delta_value: float | None
    magnitude_score: float | None
    absorbed_by_cpm: bool
    qualification: str | None
    detected_at: str

    cause_rule_id: str | None
    cause_label: str | None
    cause_domain: str | None
    cause_posterior: float | None

    decision_id: int | None
    action_level: str | None
    decision_source: str | None  # tolerance | memory_shortcut
    triggered_at: str | None
    latency_minutes: int | None

    approval_id: int | None
    approval_status: str | None  # pending | approved | rejected | auto_timeout
    approval_level: str | None  # L1..L4
    approval_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CausalTraceSummary:
    n_deviations: int
    n_with_cause: int
    n_with_decision: int
    n_escalated: int
    n_absorbed_cpm: int
    action_level_counts: dict[str, int] = field(default_factory=dict)
    cause_counts: dict[str, int] = field(default_factory=dict)
    source_counts: dict[str, int] = field(default_factory=dict)


def _has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    try:
        cur = conn.execute(f"PRAGMA table_info({table})")
        return any(r[1] == col for r in cur.fetchall())
    except sqlite3.Error:
        return False


def export_causal_trace(db_path: Path) -> list[CausalTraceRow]:
    """Reconstitue la chaîne causale complète à partir d'un DB run.

    Chaque row = une déviation, jointe à sa (meilleure) cause hypothèse,
    sa décision filtre dual et l'entrée approval_queue si escalade.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows: list[CausalTraceRow] = []
    approval_available = _has_column(conn, "approval_queue", "decision_id")

    devs = conn.execute("""
        SELECT d.deviation_id, d.candidate_id, d.deviation_kind,
               d.delta_value, d.score, d.is_absorbed, d.qualification,
               d.detected_at
        FROM event_deviations d
        ORDER BY d.deviation_id
    """).fetchall()
    for d in devs:
        cause = conn.execute("""
            SELECT edc.rule_id, edc.posterior, edc.score,
                   rcr.label, rcr.domain
            FROM event_deviation_causes edc
            LEFT JOIN root_cause_rules rcr
              ON rcr.rule_id = edc.rule_id
             AND rcr.version = edc.rule_version
            WHERE edc.deviation_id = ?
            ORDER BY COALESCE(edc.posterior, edc.score) DESC
            LIMIT 1
        """, (d["deviation_id"],)).fetchone()

        decision = conn.execute("""
            SELECT decision_id, action_level, source, triggered_at,
                   latency_minutes
            FROM tolerance_filter_decisions
            WHERE deviation_id = ?
            ORDER BY decision_id DESC
            LIMIT 1
        """, (d["deviation_id"],)).fetchone()

        approval = None
        if approval_available and decision is not None:
            approval = conn.execute("""
                SELECT queue_id AS approval_id, status,
                       autonomy_level, notes
                FROM approval_queue
                WHERE decision_id = ?
                ORDER BY queue_id DESC
                LIMIT 1
            """, (decision["decision_id"],)).fetchone()

        rows.append(CausalTraceRow(
            deviation_id=int(d["deviation_id"]),
            candidate_id=d["candidate_id"],
            deviation_kind=d["deviation_kind"],
            delta_value=(
                float(d["delta_value"]) if d["delta_value"] is not None
                else None
            ),
            magnitude_score=(
                float(d["score"]) if d["score"] is not None else None
            ),
            absorbed_by_cpm=bool(d["is_absorbed"]),
            qualification=d["qualification"],
            detected_at=d["detected_at"],
            cause_rule_id=cause["rule_id"] if cause else None,
            cause_label=cause["label"] if cause else None,
            cause_domain=cause["domain"] if cause else None,
            cause_posterior=(
                float(cause["posterior"] or cause["score"])
                if cause else None
            ),
            decision_id=(
                int(decision["decision_id"]) if decision else None
            ),
            action_level=decision["action_level"] if decision else None,
            decision_source=decision["source"] if decision else None,
            triggered_at=decision["triggered_at"] if decision else None,
            latency_minutes=(
                int(decision["latency_minutes"]) if decision else None
            ),
            approval_id=(
                int(approval["approval_id"]) if approval else None
            ),
            approval_status=approval["status"] if approval else None,
            approval_level=(
                approval["autonomy_level"] if approval else None
            ),
            approval_reason=approval["notes"] if approval else None,
        ))
    conn.close()
    return rows


def summarize_trace(rows: list[CausalTraceRow]) -> CausalTraceSummary:
    action_counts: dict[str, int] = {}
    cause_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    n_with_cause = n_with_decision = n_escalated = n_absorbed = 0
    for r in rows:
        if r.absorbed_by_cpm:
            n_absorbed += 1
        if r.cause_rule_id:
            n_with_cause += 1
            cause_counts[r.cause_rule_id] = (
                cause_counts.get(r.cause_rule_id, 0) + 1
            )
        if r.action_level:
            n_with_decision += 1
            action_counts[r.action_level] = (
                action_counts.get(r.action_level, 0) + 1
            )
        if r.decision_source:
            source_counts[r.decision_source] = (
                source_counts.get(r.decision_source, 0) + 1
            )
        if r.approval_id is not None:
            n_escalated += 1
    return CausalTraceSummary(
        n_deviations=len(rows),
        n_with_cause=n_with_cause,
        n_with_decision=n_with_decision,
        n_escalated=n_escalated,
        n_absorbed_cpm=n_absorbed,
        action_level_counts=action_counts,
        cause_counts=cause_counts,
        source_counts=source_counts,
    )


def write_trace_csv(rows: list[CausalTraceRow], out_path: Path) -> None:
    import csv
    if not rows:
        out_path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].to_dict().keys())
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow(r.to_dict())
