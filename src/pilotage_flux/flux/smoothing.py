"""Distribution lissée des lancements d'un contrat de flux.

V1.4 : lissage uniforme proportionnel aux quantités. Chaque candidate reçoit
un `offset_minutes` depuis `horizon_start` pour étaler les démarrages dans
l'horizon. Le takt cible du contrat module l'espacement.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from pilotage_flux.flux.contracts import (
    fetch_contract,
    fetch_version,
    get_candidates_in_version,
)


@dataclass(frozen=True)
class SmoothedLaunch:
    candidate_id: str
    offset_minutes: int
    planned_start: str


def _horizon_total_minutes(start: str, end: str) -> int:
    try:
        d_start = datetime.fromisoformat(start)
        d_end = datetime.fromisoformat(end)
    except ValueError:
        return 0
    delta = d_end - d_start
    return max(int(delta.total_seconds() // 60), 1)


def compute_smoothing(
    conn: sqlite3.Connection, contract_id: str, version: int | None = None
) -> list[SmoothedLaunch]:
    """Calcule la distribution lissée et la persiste dans flux_smoothed_launches.

    Algorithme V1.4 (simple, déterministe) : on étale les démarrages sur
    l'horizon total, espacés proportionnellement aux quantités cumulées.
    L'offset_minutes du i-ème candidate = (sum_qty[0..i] / total) × horizon.
    """
    contract = fetch_contract(conn, contract_id)
    if contract is None:
        raise ValueError(f"Contrat inconnu : {contract_id}")
    if version is None:
        version = contract.current_version
    ver = fetch_version(conn, contract_id, version)
    if ver is None:
        raise ValueError(f"Version {version} inconnue pour {contract_id}")

    candidates = get_candidates_in_version(conn, contract_id, version)
    if not candidates:
        return []

    total_qty = float(ver.total_quantity)
    horizon_min = _horizon_total_minutes(
        contract.horizon_start, contract.horizon_end
    )
    if total_qty <= 0 or horizon_min <= 0:
        return []

    start_dt = datetime.fromisoformat(contract.horizon_start)

    # Calcul cumulatif : le i-ème candidate démarre quand on a déjà engagé
    # somme(qty[0..i-1]) sur le total.
    conn.execute(
        "DELETE FROM flux_smoothed_launches WHERE contract_id = ? AND version = ?",
        (contract_id, version),
    )
    out: list[SmoothedLaunch] = []
    running = 0.0
    for cand in candidates:
        qty = float(cand["qty_in_contract"])
        offset_min = int(round((running / total_qty) * horizon_min))
        planned_dt = start_dt + timedelta(minutes=offset_min)
        planned_start_iso = planned_dt.isoformat(sep=" ")
        conn.execute(
            """
            INSERT INTO flux_smoothed_launches
                (contract_id, version, candidate_id, offset_minutes, planned_start)
            VALUES (?, ?, ?, ?, ?)
            """,
            (contract_id, version, cand["candidate_id"], offset_min, planned_start_iso),
        )
        out.append(
            SmoothedLaunch(
                candidate_id=cand["candidate_id"],
                offset_minutes=offset_min,
                planned_start=planned_start_iso,
            )
        )
        running += qty

    return out


def get_smoothed_launches(
    conn: sqlite3.Connection, contract_id: str, version: int | None = None
) -> list[SmoothedLaunch]:
    if version is None:
        contract = fetch_contract(conn, contract_id)
        if contract is None:
            return []
        version = contract.current_version
    rows = conn.execute(
        """
        SELECT candidate_id, offset_minutes, planned_start
        FROM flux_smoothed_launches
        WHERE contract_id = ? AND version = ?
        ORDER BY offset_minutes ASC
        """,
        (contract_id, version),
    ).fetchall()
    return [
        SmoothedLaunch(
            candidate_id=r["candidate_id"],
            offset_minutes=int(r["offset_minutes"]),
            planned_start=r["planned_start"],
        )
        for r in rows
    ]
