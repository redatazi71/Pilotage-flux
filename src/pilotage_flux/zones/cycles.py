"""Cycles territoriaux pour les portes P2 et P3.

Un `gate_cycle` est une instance d'évaluation d'une porte sur une période.
Exemples :
  P2-2026-07  : cycle mensuel P2 pour juillet 2026
  P3-2026-W27 : cycle hebdomadaire P3 pour la semaine 27 de 2026

Cadence par défaut (lue depuis `parameters`, fallback aux constantes ci-dessous) :
  P2 : 30 jours (mensuel)
  P3 : 7 jours (hebdomadaire)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime


DEFAULT_CADENCE_DAYS = {"P2": 30, "P3": 7}


@dataclass(frozen=True)
class GateCycle:
    cycle_id: str
    gate: str
    period_start: str
    period_end: str
    cadence_days: int
    status: str
    created_at: str
    opened_at: str | None
    closed_at: str | None


def _row_to_cycle(row: sqlite3.Row) -> GateCycle:
    return GateCycle(
        cycle_id=row["cycle_id"],
        gate=row["gate"],
        period_start=row["period_start"],
        period_end=row["period_end"],
        cadence_days=int(row["cadence_days"]),
        status=row["status"],
        created_at=row["created_at"],
        opened_at=row["opened_at"],
        closed_at=row["closed_at"],
    )


def _parametered_cadence(conn: sqlite3.Connection, gate: str) -> int:
    """Lit la cadence en jours pour une porte depuis `parameters`,
    avec fallback aux constantes DEFAULT_CADENCE_DAYS.
    """
    row = conn.execute(
        """
        SELECT value_num FROM parameters
        WHERE scope = 'global' AND name = ? AND valid_to IS NULL
        ORDER BY version DESC LIMIT 1
        """,
        (f"gate_{gate.lower()}_cadence_days",),
    ).fetchone()
    if row and row["value_num"] is not None:
        return int(row["value_num"])
    return DEFAULT_CADENCE_DAYS.get(gate, 7)


def create_cycle(
    conn: sqlite3.Connection,
    *,
    gate: str,
    cycle_id: str,
    period_start: str,
    period_end: str,
    cadence_days: int | None = None,
) -> GateCycle:
    """Crée un cycle en statut 'planned' pour une porte donnée.

    Lève ValueError si la porte n'est pas dans {P2, P3} ou si le cycle_id
    existe déjà.
    """
    if gate not in {"P2", "P3"}:
        raise ValueError(f"Porte inconnue : {gate!r} (attendu P2 ou P3)")
    if cadence_days is None:
        cadence_days = _parametered_cadence(conn, gate)

    existing = conn.execute(
        "SELECT 1 FROM gate_cycles WHERE cycle_id = ?", (cycle_id,)
    ).fetchone()
    if existing:
        raise ValueError(f"Cycle {cycle_id!r} existe déjà")

    conn.execute(
        """
        INSERT INTO gate_cycles
            (cycle_id, gate, period_start, period_end, cadence_days, status)
        VALUES (?, ?, ?, ?, ?, 'planned')
        """,
        (cycle_id, gate, period_start, period_end, cadence_days),
    )
    return _row_to_cycle(
        conn.execute(
            "SELECT * FROM gate_cycles WHERE cycle_id = ?", (cycle_id,)
        ).fetchone()
    )


def open_cycle(conn: sqlite3.Connection, cycle_id: str) -> GateCycle:
    """Passe un cycle 'planned' à 'open'.

    Lève ValueError si le cycle n'existe pas ou n'est pas dans le bon statut.
    `opened_at` est stocké avec une précision milliseconde pour ordonner les
    opens consécutifs.
    """
    row = conn.execute(
        "SELECT * FROM gate_cycles WHERE cycle_id = ?", (cycle_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Cycle inconnu : {cycle_id}")
    if row["status"] != "planned":
        raise ValueError(
            f"Cycle {cycle_id} en statut {row['status']!r}, attendu 'planned'"
        )
    conn.execute(
        """
        UPDATE gate_cycles
        SET status = 'open',
            opened_at = strftime('%Y-%m-%d %H:%M:%f', 'now')
        WHERE cycle_id = ?
        """,
        (cycle_id,),
    )
    return _row_to_cycle(
        conn.execute(
            "SELECT * FROM gate_cycles WHERE cycle_id = ?", (cycle_id,)
        ).fetchone()
    )


def close_cycle(conn: sqlite3.Connection, cycle_id: str) -> GateCycle:
    """Passe un cycle 'open' à 'closed'."""
    row = conn.execute(
        "SELECT * FROM gate_cycles WHERE cycle_id = ?", (cycle_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Cycle inconnu : {cycle_id}")
    if row["status"] != "open":
        raise ValueError(
            f"Cycle {cycle_id} en statut {row['status']!r}, attendu 'open'"
        )
    conn.execute(
        """
        UPDATE gate_cycles
        SET status = 'closed', closed_at = datetime('now')
        WHERE cycle_id = ?
        """,
        (cycle_id,),
    )
    return _row_to_cycle(
        conn.execute(
            "SELECT * FROM gate_cycles WHERE cycle_id = ?", (cycle_id,)
        ).fetchone()
    )


def current_open_cycle(
    conn: sqlite3.Connection, gate: str
) -> GateCycle | None:
    """Renvoie le cycle 'open' courant pour une porte, ou None.

    Departage les `opened_at` ex-aequo par `rowid DESC` (ordre d'insertion).
    """
    row = conn.execute(
        """
        SELECT * FROM gate_cycles
        WHERE gate = ? AND status = 'open'
        ORDER BY opened_at DESC, rowid DESC
        LIMIT 1
        """,
        (gate,),
    ).fetchone()
    return _row_to_cycle(row) if row else None


def list_cycles(
    conn: sqlite3.Connection,
    *,
    gate: str | None = None,
    status: str | None = None,
) -> list[GateCycle]:
    """Liste les cycles, filtrable par porte et/ou statut."""
    sql = "SELECT * FROM gate_cycles WHERE 1=1"
    params: list[str] = []
    if gate is not None:
        sql += " AND gate = ?"
        params.append(gate)
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY period_start ASC, cycle_id ASC"
    return [_row_to_cycle(r) for r in conn.execute(sql, params)]
