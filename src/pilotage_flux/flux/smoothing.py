"""Distribution lissée des lancements d'un contrat de flux.

V1.4 : lissage uniforme proportionnel aux quantités. Chaque candidate reçoit
un `offset_minutes` depuis `horizon_start` pour étaler les démarrages dans
l'horizon. Le takt cible du contrat module l'espacement.

**V12.6 — Due-date aware** : si le paramètre global
`smoothing_due_date_aware` vaut 1, chaque offset est borné par
`latest_start = due_date - duration` du SO parent. Ceci réconcilie le
flux avec l'objectif OTIF (§30) au prix d'un smoothing moins étalé
sur les SOs à due_date courte. Corrige le défaut structurel §24.8.7.
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
from pilotage_flux.parameters import get_num


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


def _get_due_date_aware_flag(conn: sqlite3.Connection) -> bool:
    """V12.6 — Lit le paramètre `smoothing_due_date_aware` (default 0).

    1 = active la version V12.6 (offsets bornés par latest_start)
    0 = version V1.4 historique (smoothing libre sur l'horizon)
    """
    val = get_num(
        conn, scope="global", scope_ref=None,
        name="smoothing_due_date_aware", default=0.0,
    )
    return bool(val and float(val) > 0.5)


def _compute_latest_start_minutes(
    conn: sqlite3.Connection,
    candidate_id: str,
    horizon_start: str,
    fallback_min: int,
) -> int:
    """V12.6 — Calcule le `latest_start_minutes` d'un candidat.

    `latest_start = (due_date - duration_estimée) − horizon_start`
    en minutes. Si la candidate n'a pas de SO parent ou pas de due_date,
    on renvoie `fallback_min` (= horizon total → smoothing libre).

    duration_estimée : on prend la somme des unit_time_min des
    operations du candidate (via les routings, agrégée par article).
    À défaut, fallback 480 min/jour × 2 = 960 min.
    """
    row = conn.execute(
        """
        SELECT so.due_date, c.article_id
        FROM candidate_orders c
        JOIN sales_orders so ON so.sales_order_id = c.sales_order_id
        WHERE c.candidate_id = ?
        """,
        (candidate_id,),
    ).fetchone()
    if row is None or not row["due_date"]:
        return fallback_min

    try:
        due_dt = datetime.fromisoformat(row["due_date"])
        start_dt = datetime.fromisoformat(horizon_start)
    except (ValueError, TypeError):
        return fallback_min

    # Estime la durée totale du candidate via routings
    dur_row = conn.execute(
        """
        SELECT COALESCE(SUM(unit_time_min), 0) AS total_min
        FROM routing_operations
        WHERE article_id = ?
        """,
        (row["article_id"],),
    ).fetchone()
    duration_min = int(dur_row["total_min"] or 960) if dur_row else 960
    if duration_min < 60:
        duration_min = 60  # plancher pratique

    latest_start_min = int(
        (due_dt - start_dt).total_seconds() // 60
    ) - duration_min
    return max(0, latest_start_min)


def compute_smoothing(
    conn: sqlite3.Connection, contract_id: str, version: int | None = None
) -> list[SmoothedLaunch]:
    """Calcule la distribution lissée et la persiste dans flux_smoothed_launches.

    Algorithme V1.4 (simple, déterministe) : on étale les démarrages sur
    l'horizon total, espacés proportionnellement aux quantités cumulées.
    L'offset_minutes du i-ème candidate = (sum_qty[0..i] / total) × horizon.

    V12.6 (data-driven, via `smoothing_due_date_aware = 1`) : chaque
    offset est borné par `latest_start = due_date - duration` afin de
    garantir que la livraison reste possible avant la due_date.
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
    due_date_aware = _get_due_date_aware_flag(conn)

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
        linear_offset = int(round((running / total_qty) * horizon_min))
        if due_date_aware:
            latest_start = _compute_latest_start_minutes(
                conn,
                candidate_id=cand["candidate_id"],
                horizon_start=contract.horizon_start,
                fallback_min=horizon_min,
            )
            # V12.6 : on borne par latest_start. Si la valeur cible
            # linéaire dépasse latest_start, on recale pour respecter
            # la due_date.
            offset_min = min(linear_offset, latest_start)
        else:
            offset_min = linear_offset
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
