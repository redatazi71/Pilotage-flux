"""Accesseurs des parametres data-driven stockes en SQLite.

Toutes les capacites, rendements et seuils sont en table `parameters`.
Aucune valeur metier ne doit etre codee en dur ailleurs dans le code.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Parameter:
    scope: str
    scope_ref: str | None
    name: str
    value_num: float | None
    value_text: str | None


def get_num(
    conn: sqlite3.Connection,
    *,
    scope: str,
    scope_ref: str | None,
    name: str,
    default: float | None = None,
) -> float | None:
    """Lit la valeur numerique d'un parametre courant (`valid_to IS NULL`)."""
    row = conn.execute(
        """
        SELECT value_num FROM parameters
        WHERE scope = ?
          AND (scope_ref IS ? OR scope_ref = ?)
          AND name = ?
          AND valid_to IS NULL
        ORDER BY version DESC
        LIMIT 1
        """,
        (scope, scope_ref, scope_ref, name),
    ).fetchone()
    if row is None:
        return default
    return row["value_num"]


def get_text(
    conn: sqlite3.Connection,
    *,
    scope: str,
    scope_ref: str | None,
    name: str,
    default: str | None = None,
) -> str | None:
    """Lit la valeur texte d'un paramètre courant (`valid_to IS NULL`)."""
    row = conn.execute(
        """
        SELECT value_text FROM parameters
        WHERE scope = ?
          AND (scope_ref IS ? OR scope_ref = ?)
          AND name = ?
          AND valid_to IS NULL
        ORDER BY version DESC
        LIMIT 1
        """,
        (scope, scope_ref, scope_ref, name),
    ).fetchone()
    if row is None:
        return default
    return row["value_text"]


def workstation_capacity_factor(
    conn: sqlite3.Connection, workstation_id: str
) -> float:
    """Coefficient de capacite pour un poste (defaut 1.0 si absent)."""
    val = get_num(
        conn,
        scope="workstation",
        scope_ref=workstation_id,
        name="capacity_factor",
        default=1.0,
    )
    return float(val) if val is not None else 1.0


def workstation_yield_rate(
    conn: sqlite3.Connection, workstation_id: str
) -> float:
    """Taux de rendement matiere par poste (defaut 1.0 si absent)."""
    val = get_num(
        conn,
        scope="workstation",
        scope_ref=workstation_id,
        name="yield_rate",
        default=1.0,
    )
    return float(val) if val is not None else 1.0
