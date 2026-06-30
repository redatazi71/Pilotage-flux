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

    1 = active la version V12.6 (offsets bornés par latest_start_due)
    0 = version V1.4 historique (smoothing libre sur l'horizon)
    """
    val = get_num(
        conn, scope="global", scope_ref=None,
        name="smoothing_due_date_aware", default=0.0,
    )
    return bool(val and float(val) > 0.5)


def _get_horizon_aware_flag(conn: sqlite3.Connection) -> bool:
    """V12.7 — Lit le paramètre `smoothing_horizon_aware` (default 0).

    Borne l'offset à `horizon_end - duration × safety_factor` pour
    garantir que l'OF termine avant la fin de simulation. Corrige le
    vrai défaut Q identifié par §28.13 (V12.6 invalidé, V12.7 cible
    la vraie cause).
    """
    val = get_num(
        conn, scope="global", scope_ref=None,
        name="smoothing_horizon_aware", default=0.0,
    )
    return bool(val and float(val) > 0.5)


def _get_horizon_safety_factor(conn: sqlite3.Connection) -> float:
    """V12.7 — Lit `smoothing_horizon_safety_factor` (default 10.0).

    Multiplicateur appliqué à la durée estimée pour absorber
    l'attente sur workstations (queueing) que la simple somme
    unit_time × quantity n'inclut pas. Doit être > 1.
    """
    val = get_num(
        conn, scope="global", scope_ref=None,
        name="smoothing_horizon_safety_factor", default=10.0,
    )
    f = float(val) if val is not None else 10.0
    return max(1.0, f)


def _estimate_candidate_duration_min(
    conn: sqlite3.Connection, candidate_id: str
) -> int:
    """Estime la durée totale du candidate.

    durée = quantity × Σ(unit_time_min des operations du routing).
    Fallback : 960 min. Plancher : 60 min.

    Note : ne modélise pas l'attente sur les workstations (queueing).
    Un multiplicateur de sécurité est ajouté pour les contextes
    multi-OFs (voir `compute_smoothing`).
    """
    row = conn.execute(
        """
        SELECT
            COALESCE(c.quantity, 1) AS qty,
            COALESCE(SUM(r.unit_time_min), 0) AS unit_total
        FROM candidate_orders c
        LEFT JOIN routing_operations r ON r.article_id = c.article_id
        WHERE c.candidate_id = ?
        GROUP BY c.candidate_id
        """,
        (candidate_id,),
    ).fetchone()
    if row is None:
        return 960
    qty = float(row["qty"] or 1.0)
    unit_total = float(row["unit_total"] or 0.0)
    duration_min = int(round(qty * unit_total)) if unit_total > 0 else 960
    if duration_min < 60:
        duration_min = 60
    return duration_min


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
    """
    row = conn.execute(
        """
        SELECT so.due_date
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

    duration_min = _estimate_candidate_duration_min(conn, candidate_id)
    latest_start_min = int(
        (due_dt - start_dt).total_seconds() // 60
    ) - duration_min
    return max(0, latest_start_min)


def _compute_latest_start_horizon_minutes(
    conn: sqlite3.Connection,
    candidate_id: str,
    horizon_total_min: int,
    safety_factor: float = 10.0,
) -> int:
    """V12.7 — Calcule l'offset maximal tel que l'OF puisse terminer
    avant `horizon_end`, incluant un facteur d'attente queueing.

    `latest_start_horizon = horizon_total_min - duration × safety_factor`.

    Le multiplicateur `safety_factor` couvre l'écart entre temps de
    cycle Σ(unit_time × qty) et temps elapsed réel (queueing,
    setups, transferts inter-WS). Valeur typique : 10-50× selon le
    taux d'utilisation des workstations.
    """
    duration_min = _estimate_candidate_duration_min(conn, candidate_id)
    effective_duration = int(round(duration_min * max(1.0, safety_factor)))
    return max(0, horizon_total_min - effective_duration)


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

    V12.7 (via `smoothing_horizon_aware = 1`) : chaque offset est borné
    par `horizon_end - duration` pour garantir que l'OF termine avant
    la fin de simulation. Cible la vraie cause du défaut Q (OFs stuck
    `in_progress` avec qty_good=0) identifiée par §28.13 après que
    V12.6 a été invalidé.
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
    horizon_aware = _get_horizon_aware_flag(conn)
    safety_factor = (
        _get_horizon_safety_factor(conn) if horizon_aware else 1.0
    )

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
        offset_min = linear_offset
        if due_date_aware:
            latest_start_due = _compute_latest_start_minutes(
                conn,
                candidate_id=cand["candidate_id"],
                horizon_start=contract.horizon_start,
                fallback_min=horizon_min,
            )
            offset_min = min(offset_min, latest_start_due)
        if horizon_aware:
            # V12.7 : borne par horizon_end - duration × safety pour
            # que l'OF ait le temps de terminer avant la fin de
            # simulation (incluant l'attente queueing). Corrige la
            # vraie cause du défaut Q identifiée par §28.13.
            latest_start_horizon = _compute_latest_start_horizon_minutes(
                conn,
                candidate_id=cand["candidate_id"],
                horizon_total_min=horizon_min,
                safety_factor=safety_factor,
            )
            offset_min = min(offset_min, latest_start_horizon)
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
