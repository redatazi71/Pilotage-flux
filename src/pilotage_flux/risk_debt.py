"""Registre des risk_debts (cadrage §7 bis.3).

Une risk_debt est un engagement formellement accepte avec un risque non leve,
cree a la porte P2 par decision PASS_WITH_RISK et obligatoirement eteint avant
P3 ou avant execution.

V1.3 : creation, listing, extinction manuelle, expiration sur deadline.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from pilotage_flux.parameters import get_num


@dataclass(frozen=True)
class RiskDebt:
    risk_debt_id: int
    candidate_id: str
    criterion: str
    rule_id: str
    score: float
    deadline: str
    status: str
    explanation: str | None
    created_at: str
    extinguished_at: str | None
    extinction_reason: str | None


def _row(row: sqlite3.Row) -> RiskDebt:
    return RiskDebt(
        risk_debt_id=int(row["risk_debt_id"]),
        candidate_id=row["candidate_id"],
        criterion=row["criterion"],
        rule_id=row["rule_id"],
        score=float(row["score"]),
        deadline=row["deadline"],
        status=row["status"],
        explanation=row["explanation"],
        created_at=row["created_at"],
        extinguished_at=row["extinguished_at"],
        extinction_reason=row["extinction_reason"],
    )


def _default_deadline_days(conn: sqlite3.Connection) -> int:
    val = get_num(
        conn,
        scope="global",
        scope_ref=None,
        name="risk_debt_default_deadline_days",
        default=7,
    )
    return int(val) if val is not None else 7


def open_risk_debt(
    conn: sqlite3.Connection,
    *,
    candidate_id: str,
    criterion: str,
    rule_id: str,
    score: float,
    deadline: str | None = None,
    explanation: str | None = None,
) -> RiskDebt:
    """Cree une nouvelle risk_debt en statut 'open' pour un candidate."""
    if deadline is None:
        days = _default_deadline_days(conn)
        deadline = (date.today() + timedelta(days=days)).isoformat()

    cur = conn.execute(
        """
        INSERT INTO risk_debt_register
            (candidate_id, criterion, rule_id, score, deadline, status, explanation)
        VALUES (?, ?, ?, ?, ?, 'open', ?)
        """,
        (candidate_id, criterion, rule_id, score, deadline, explanation),
    )
    rid = cur.lastrowid
    assert rid is not None
    row = conn.execute(
        "SELECT * FROM risk_debt_register WHERE risk_debt_id = ?", (rid,)
    ).fetchone()
    return _row(row)


def extinguish_risk_debt(
    conn: sqlite3.Connection,
    risk_debt_id: int,
    *,
    reason: str,
) -> RiskDebt:
    """Eteint une risk_debt (statut 'open' -> 'extinct')."""
    row = conn.execute(
        "SELECT * FROM risk_debt_register WHERE risk_debt_id = ?",
        (risk_debt_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Risk_debt inconnue : {risk_debt_id}")
    if row["status"] != "open":
        raise ValueError(
            f"Risk_debt {risk_debt_id} en statut {row['status']!r}, attendu 'open'"
        )
    conn.execute(
        """
        UPDATE risk_debt_register
        SET status = 'extinct',
            extinguished_at = datetime('now'),
            extinction_reason = ?
        WHERE risk_debt_id = ?
        """,
        (reason, risk_debt_id),
    )
    return _row(
        conn.execute(
            "SELECT * FROM risk_debt_register WHERE risk_debt_id = ?",
            (risk_debt_id,),
        ).fetchone()
    )


def expire_overdue_risk_debts(
    conn: sqlite3.Connection, today: date | None = None
) -> int:
    """Passe toutes les risk_debts 'open' avec deadline depassee en 'expired'.

    Renvoie le nombre de risk_debts expirees.
    """
    if today is None:
        today = date.today()
    cur = conn.execute(
        """
        UPDATE risk_debt_register
        SET status = 'expired',
            extinguished_at = datetime('now'),
            extinction_reason = 'deadline_passed'
        WHERE status = 'open' AND date(deadline) < date(?)
        """,
        (today.isoformat(),),
    )
    return cur.rowcount


def list_risk_debts(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    candidate_id: str | None = None,
) -> list[RiskDebt]:
    sql = "SELECT * FROM risk_debt_register WHERE 1=1"
    params: list[str] = []
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    if candidate_id is not None:
        sql += " AND candidate_id = ?"
        params.append(candidate_id)
    sql += " ORDER BY risk_debt_id ASC"
    return [_row(r) for r in conn.execute(sql, params)]


def has_open_debt(conn: sqlite3.Connection, candidate_id: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM risk_debt_register
        WHERE candidate_id = ? AND status = 'open'
        LIMIT 1
        """,
        (candidate_id,),
    ).fetchone()
    return row is not None
