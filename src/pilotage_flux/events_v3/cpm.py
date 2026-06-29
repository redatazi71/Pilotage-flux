"""Niveau 0 CPM - absorption d'écart par marge de sécurité (L3.3).

Conforme §11 du cadrage : "Écart absorbé par marge de sécurité CPM :
informer uniquement". Un écart temporel dont la magnitude reste dans
la marge CPM ne déclenche pas d'action — il est marqué is_absorbed
et qualifié 'absorbed'.

La marge est data-driven via parameters (cpm_margin_minutes par défaut,
peut être override par workstation via scope='workstation' + scope_ref=ws_id).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pilotage_flux.parameters import get_num


DEFAULT_MARGIN_MINUTES = 60.0


@dataclass(frozen=True)
class CpmAbsorption:
    deviation_id: int
    absolute_delta_minutes: float
    margin_minutes: float
    margin_used: float
    is_absorbed: bool
    new_qualification: str


def _margin_for(conn: sqlite3.Connection, workstation_id: str | None) -> float:
    """Récupère la marge CPM, par poste si défini, sinon globale."""
    if workstation_id:
        val = get_num(
            conn,
            scope="workstation",
            scope_ref=workstation_id,
            name="cpm_margin_minutes",
            default=None,
        )
        if val is not None:
            return float(val)
    val = get_num(
        conn,
        scope="global",
        scope_ref=None,
        name="cpm_margin_minutes",
        default=DEFAULT_MARGIN_MINUTES,
    )
    return float(val) if val is not None else DEFAULT_MARGIN_MINUTES


def apply_cpm_absorption(
    conn: sqlite3.Connection, *, batch_id: str | None = None
) -> list[CpmAbsorption]:
    """Applique l'absorption CPM aux écarts temporels non encore évalués.

    Pour chaque deviation de kind='time_delta' non absorbée :
      - Récupère le workstation_id via expected_event
      - Lit la marge applicable (poste ou globale)
      - Si |delta_value| <= margin -> is_absorbed=1, qualification='absorbed',
        cpm_margin_used = |delta_value|
      - Sinon, ne touche pas (qualification déjà calculée par matching)

    `batch_id` optionnel limite aux deviations d'une tranche.
    """
    sql = """
        SELECT d.deviation_id, d.delta_value, e.workstation_id
        FROM event_deviations d
        LEFT JOIN expected_events e ON e.expected_event_id = d.expected_event_id
        WHERE d.deviation_kind = 'time_delta'
          AND d.is_absorbed = 0
          AND d.cpm_margin_used IS NULL
    """
    params: list[str] = []
    if batch_id is not None:
        sql += " AND e.batch_id = ?"
        params.append(batch_id)

    rows = conn.execute(sql, params).fetchall()
    out: list[CpmAbsorption] = []
    for r in rows:
        if r["delta_value"] is None:
            continue
        abs_delta = abs(float(r["delta_value"]))
        margin = _margin_for(conn, r["workstation_id"])
        absorbed = abs_delta <= margin
        if absorbed:
            new_qualif = "absorbed"
            margin_used = abs_delta
        else:
            new_qualif = None  # garde la qualification existante
            margin_used = margin  # marge intégralement consommée

        conn.execute(
            """
            UPDATE event_deviations
            SET cpm_margin_used = ?,
                is_absorbed = ?,
                qualification = COALESCE(?, qualification)
            WHERE deviation_id = ?
            """,
            (margin_used, 1 if absorbed else 0, new_qualif, r["deviation_id"]),
        )
        out.append(
            CpmAbsorption(
                deviation_id=int(r["deviation_id"]),
                absolute_delta_minutes=abs_delta,
                margin_minutes=margin,
                margin_used=margin_used,
                is_absorbed=absorbed,
                new_qualification=new_qualif or "(unchanged)",
            )
        )
    return out
