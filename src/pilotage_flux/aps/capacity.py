"""Calcul de la charge et de la capacite par poste, V0.

La capacite disponible par jour d'un poste est :
    daily_minutes (calendrier) x capacity_factor (parametres)

La charge induite par un candidate_order sur un poste est :
    quantity x unit_time_min (routing_operations)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pilotage_flux.parameters import workstation_capacity_factor


@dataclass(frozen=True)
class WorkstationLoad:
    workstation_id: str
    label: str
    load_minutes: float
    daily_capacity_minutes: float
    is_overloaded: bool


def _default_calendar_minutes(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT daily_minutes FROM calendars ORDER BY calendar_id ASC LIMIT 1"
    ).fetchone()
    if row is None:
        return 0
    return int(row["daily_minutes"])


def compute_load_by_workstation(
    conn: sqlite3.Connection,
    *,
    candidate_ids: list[str] | None = None,
) -> list[WorkstationLoad]:
    """Charge induite par les candidate_orders sur chaque poste.

    Si `candidate_ids` est None, la charge porte sur l'ensemble des
    candidate_orders en statut 'candidate' ou 'promoted'.
    """
    if candidate_ids is None:
        rows = conn.execute(
            """
            SELECT co.candidate_id, co.article_id, co.quantity,
                   ro.workstation_id, ro.unit_time_min
            FROM candidate_orders AS co
            JOIN routing_operations AS ro
              ON ro.article_id = co.article_id
            WHERE co.status IN ('candidate', 'promoted')
            """
        ).fetchall()
    else:
        if not candidate_ids:
            return []
        placeholders = ",".join("?" * len(candidate_ids))
        rows = conn.execute(
            f"""
            SELECT co.candidate_id, co.article_id, co.quantity,
                   ro.workstation_id, ro.unit_time_min
            FROM candidate_orders AS co
            JOIN routing_operations AS ro
              ON ro.article_id = co.article_id
            WHERE co.candidate_id IN ({placeholders})
            """,
            candidate_ids,
        ).fetchall()

    load_per_ws: dict[str, float] = {}
    for r in rows:
        load_per_ws.setdefault(r["workstation_id"], 0.0)
        load_per_ws[r["workstation_id"]] += float(r["quantity"]) * float(
            r["unit_time_min"]
        )

    ws_rows = conn.execute(
        "SELECT workstation_id, label FROM workstations ORDER BY sequence_idx ASC"
    ).fetchall()

    daily_minutes = _default_calendar_minutes(conn)
    out: list[WorkstationLoad] = []
    for w in ws_rows:
        wid = w["workstation_id"]
        factor = workstation_capacity_factor(conn, wid)
        capacity = daily_minutes * factor
        load = load_per_ws.get(wid, 0.0)
        out.append(
            WorkstationLoad(
                workstation_id=wid,
                label=w["label"],
                load_minutes=load,
                daily_capacity_minutes=capacity,
                is_overloaded=load > capacity if capacity > 0 else False,
            )
        )
    return out
