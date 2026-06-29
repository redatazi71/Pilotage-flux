"""Plans de controle et evenements qualite (V2)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


EVT_CONTROL_PASS = "control_pass"
EVT_CONTROL_FAIL = "control_fail"
EVT_NC_OPENED = "nc_opened"
EVT_NC_REWORK = "nc_rework"
EVT_NC_SCRAP = "nc_scrap"
EVT_RELEASE = "release"
EVT_BLOCK = "block"


@dataclass(frozen=True)
class QualityControl:
    control_id: int
    article_id: str
    label: str
    criterion: str
    sample_rate: float
    blocking: bool
    created_at: str


@dataclass(frozen=True)
class QualityEvent:
    quality_event_id: int
    of_id: str
    of_op_id: int | None
    control_id: int | None
    event_type: str
    severity: str
    qty_concerned: float | None
    explanation: str | None
    actor: str | None
    at_time: str


# -----------------------------------------------------------------------
# Plans de controle
# -----------------------------------------------------------------------

def create_control(
    conn: sqlite3.Connection,
    *,
    article_id: str,
    label: str,
    criterion: str,
    sample_rate: float = 1.0,
    blocking: bool = True,
) -> QualityControl:
    if not 0.0 < sample_rate <= 1.0:
        raise ValueError("sample_rate doit etre dans ]0, 1]")
    art = conn.execute(
        "SELECT 1 FROM articles WHERE article_id = ?", (article_id,)
    ).fetchone()
    if art is None:
        raise ValueError(f"Article inconnu : {article_id}")
    cur = conn.execute(
        """
        INSERT INTO quality_controls
            (article_id, label, criterion, sample_rate, blocking)
        VALUES (?, ?, ?, ?, ?)
        """,
        (article_id, label, criterion, sample_rate, 1 if blocking else 0),
    )
    row = conn.execute(
        "SELECT * FROM quality_controls WHERE control_id = ?",
        (cur.lastrowid,),
    ).fetchone()
    return _row_control(row)


def list_controls(
    conn: sqlite3.Connection, *, article_id: str | None = None
) -> list[QualityControl]:
    sql = "SELECT * FROM quality_controls WHERE 1=1"
    params: list[str] = []
    if article_id is not None:
        sql += " AND article_id = ?"
        params.append(article_id)
    sql += " ORDER BY control_id ASC"
    return [_row_control(r) for r in conn.execute(sql, params)]


def _row_control(row: sqlite3.Row) -> QualityControl:
    return QualityControl(
        control_id=int(row["control_id"]),
        article_id=row["article_id"],
        label=row["label"],
        criterion=row["criterion"],
        sample_rate=float(row["sample_rate"]),
        blocking=bool(row["blocking"]),
        created_at=row["created_at"],
    )


# -----------------------------------------------------------------------
# Evenements qualite
# -----------------------------------------------------------------------

def _emit_event(
    conn: sqlite3.Connection,
    *,
    of_id: str,
    event_type: str,
    of_op_id: int | None = None,
    control_id: int | None = None,
    severity: str = "normal",
    qty_concerned: float | None = None,
    explanation: str | None = None,
    actor: str | None = None,
) -> QualityEvent:
    of_row = conn.execute(
        "SELECT 1 FROM manufacturing_orders WHERE of_id = ?", (of_id,)
    ).fetchone()
    if of_row is None:
        raise ValueError(f"OF inconnu : {of_id}")

    cur = conn.execute(
        """
        INSERT INTO quality_events
            (of_id, of_op_id, control_id, event_type, severity,
             qty_concerned, explanation, actor)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (of_id, of_op_id, control_id, event_type, severity,
         qty_concerned, explanation, actor),
    )
    row = conn.execute(
        "SELECT * FROM quality_events WHERE quality_event_id = ?",
        (cur.lastrowid,),
    ).fetchone()
    return _row_event(row)


def _row_event(row: sqlite3.Row) -> QualityEvent:
    return QualityEvent(
        quality_event_id=int(row["quality_event_id"]),
        of_id=row["of_id"],
        of_op_id=int(row["of_op_id"]) if row["of_op_id"] is not None else None,
        control_id=int(row["control_id"]) if row["control_id"] is not None else None,
        event_type=row["event_type"],
        severity=row["severity"],
        qty_concerned=(
            float(row["qty_concerned"]) if row["qty_concerned"] is not None else None
        ),
        explanation=row["explanation"],
        actor=row["actor"],
        at_time=row["at_time"],
    )


def declare_control_pass(
    conn: sqlite3.Connection,
    *,
    of_id: str,
    control_id: int,
    of_op_id: int | None = None,
    qty_concerned: float | None = None,
    actor: str = "qa.operator",
) -> QualityEvent:
    return _emit_event(
        conn,
        of_id=of_id,
        of_op_id=of_op_id,
        control_id=control_id,
        event_type=EVT_CONTROL_PASS,
        qty_concerned=qty_concerned,
        actor=actor,
    )


def declare_control_fail(
    conn: sqlite3.Connection,
    *,
    of_id: str,
    control_id: int,
    of_op_id: int | None = None,
    qty_concerned: float | None = None,
    severity: str = "high",
    explanation: str | None = None,
    actor: str = "qa.operator",
) -> QualityEvent:
    """Echec controle : peut bloquer l'OF si le plan a blocking=1."""
    return _emit_event(
        conn,
        of_id=of_id,
        of_op_id=of_op_id,
        control_id=control_id,
        event_type=EVT_CONTROL_FAIL,
        severity=severity,
        qty_concerned=qty_concerned,
        explanation=explanation,
        actor=actor,
    )


def open_nc(
    conn: sqlite3.Connection,
    *,
    of_id: str,
    of_op_id: int | None = None,
    qty_concerned: float,
    severity: str = "high",
    explanation: str | None = None,
    actor: str = "qa.operator",
) -> QualityEvent:
    """Ouvre une non-conformite sur un OF."""
    if qty_concerned <= 0:
        raise ValueError("qty_concerned doit etre strictement positif")
    return _emit_event(
        conn,
        of_id=of_id,
        of_op_id=of_op_id,
        event_type=EVT_NC_OPENED,
        severity=severity,
        qty_concerned=qty_concerned,
        explanation=explanation,
        actor=actor,
    )


def rework_nc(
    conn: sqlite3.Connection,
    *,
    of_id: str,
    qty_reworked: float,
    explanation: str | None = None,
    actor: str = "qa.operator",
) -> QualityEvent:
    """Decision de retouche sur une NC."""
    return _emit_event(
        conn,
        of_id=of_id,
        event_type=EVT_NC_REWORK,
        qty_concerned=qty_reworked,
        explanation=explanation,
        actor=actor,
    )


def scrap_nc(
    conn: sqlite3.Connection,
    *,
    of_id: str,
    qty_scrapped: float,
    explanation: str | None = None,
    actor: str = "qa.operator",
) -> QualityEvent:
    """Mise au rebut suite a NC."""
    return _emit_event(
        conn,
        of_id=of_id,
        event_type=EVT_NC_SCRAP,
        qty_concerned=qty_scrapped,
        explanation=explanation,
        actor=actor,
    )


def block_of(
    conn: sqlite3.Connection,
    *,
    of_id: str,
    reason: str,
    actor: str = "qa.lead",
) -> QualityEvent:
    """Bloque un OF en attente de decision qualite."""
    return _emit_event(
        conn,
        of_id=of_id,
        event_type=EVT_BLOCK,
        severity="critical",
        explanation=reason,
        actor=actor,
    )


def release_of(
    conn: sqlite3.Connection,
    *,
    of_id: str,
    explanation: str | None = None,
    actor: str = "qa.lead",
) -> QualityEvent:
    """Libere un OF apres validation qualite."""
    return _emit_event(
        conn,
        of_id=of_id,
        event_type=EVT_RELEASE,
        explanation=explanation,
        actor=actor,
    )


def list_events(
    conn: sqlite3.Connection,
    *,
    of_id: str | None = None,
    event_type: str | None = None,
) -> list[QualityEvent]:
    sql = "SELECT * FROM quality_events WHERE 1=1"
    params: list[str] = []
    if of_id is not None:
        sql += " AND of_id = ?"
        params.append(of_id)
    if event_type is not None:
        sql += " AND event_type = ?"
        params.append(event_type)
    sql += " ORDER BY quality_event_id ASC"
    return [_row_event(r) for r in conn.execute(sql, params)]
