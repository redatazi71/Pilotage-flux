"""Vérification de cohérence d'un contrat de flux.

Deux familles de contrôles V1.4 :
  1. Charge par poste vs capacité horizon (cumul des routings × qtés)
  2. Takt contractuel vs takt goulot (le contrat doit être tenable)

Met à jour `flux_contracts.status` et `flux_contract_versions.is_coherent`.
Persiste chaque vérification dans `flux_coherence_checks`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from pilotage_flux.flux.contracts import (
    fetch_contract,
    fetch_version,
    get_candidates_in_version,
)


@dataclass(frozen=True)
class CoherenceCheck:
    workstation_id: str | None
    metric: str
    actual_value: float | None
    limit_value: float | None
    is_ok: bool
    explanation: str


@dataclass
class CoherenceReport:
    contract_id: str
    version: int
    checks: list[CoherenceCheck] = field(default_factory=list)
    overall_ok: bool = True

    @property
    def violations(self) -> list[CoherenceCheck]:
        return [c for c in self.checks if not c.is_ok]


def _horizon_minutes(conn: sqlite3.Connection, start: str, end: str) -> int:
    """Calcule les minutes ouvrées de l'horizon (calendrier + jours ouvrés)."""
    try:
        d_start = date.fromisoformat(start)
        d_end = date.fromisoformat(end)
    except ValueError:
        return 0
    calendar = conn.execute(
        "SELECT daily_minutes, working_days FROM calendars LIMIT 1"
    ).fetchone()
    if calendar is None:
        return 0
    daily_min = int(calendar["daily_minutes"])
    working_days = (calendar["working_days"] or "mon,tue,wed,thu,fri").split(",")
    day_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    working_idx = {day_map[d.strip()] for d in working_days if d.strip() in day_map}

    total = 0
    cur = d_start
    while cur <= d_end:
        if cur.weekday() in working_idx:
            total += daily_min
        cur = cur.fromordinal(cur.toordinal() + 1)
    return total


def _workstation_capacity(
    conn: sqlite3.Connection, workstation_id: str, horizon_minutes: int
) -> float:
    factor = conn.execute(
        """
        SELECT value_num FROM parameters
        WHERE scope = 'workstation' AND scope_ref = ?
          AND name = 'capacity_factor' AND valid_to IS NULL
        ORDER BY version DESC LIMIT 1
        """,
        (workstation_id,),
    ).fetchone()
    f = float(factor["value_num"]) if factor and factor["value_num"] is not None else 1.0
    return horizon_minutes * f


def _bottleneck_unit_time(
    conn: sqlite3.Connection, candidate_ids: list[str]
) -> tuple[str | None, float]:
    """Renvoie (workstation_id, max_unit_time_min) sur l'ensemble des routings
    des articles des candidates. C'est notre approximation de takt goulot."""
    if not candidate_ids:
        return None, 0.0
    placeholders = ",".join("?" * len(candidate_ids))
    row = conn.execute(
        f"""
        SELECT ro.workstation_id, ro.unit_time_min
        FROM routing_operations ro
        JOIN candidate_orders co ON co.article_id = ro.article_id
        WHERE co.candidate_id IN ({placeholders})
        ORDER BY ro.unit_time_min DESC LIMIT 1
        """,
        candidate_ids,
    ).fetchone()
    if row is None:
        return None, 0.0
    return row["workstation_id"], float(row["unit_time_min"])


def compute_coherence(
    conn: sqlite3.Connection, contract_id: str, version: int | None = None
) -> CoherenceReport:
    """Vérifie la cohérence d'une version d'un contrat.

    Effets de bord :
      - Persiste les checks dans `flux_coherence_checks`
      - Met à jour `flux_contract_versions.is_coherent`
      - Met à jour `flux_contracts.status` (coherent | incoherent)
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
    candidate_ids = [c["candidate_id"] for c in candidates]

    horizon_min = _horizon_minutes(
        conn, contract.horizon_start, contract.horizon_end
    )

    report = CoherenceReport(contract_id=contract_id, version=version)

    # Purge des anciens checks pour cette version (re-évaluation propre)
    conn.execute(
        "DELETE FROM flux_coherence_checks WHERE contract_id = ? AND version = ?",
        (contract_id, version),
    )

    # 1. Charge par poste
    load_by_ws: dict[str, float] = {}
    for cand in candidates:
        ops = conn.execute(
            """
            SELECT workstation_id, unit_time_min
            FROM routing_operations WHERE article_id = ?
            """,
            (cand["article_id"],),
        ).fetchall()
        for op in ops:
            load = float(op["unit_time_min"]) * float(cand["qty_in_contract"])
            load_by_ws.setdefault(op["workstation_id"], 0.0)
            load_by_ws[op["workstation_id"]] += load

    for wid, load in load_by_ws.items():
        capa = _workstation_capacity(conn, wid, horizon_min)
        is_ok = load <= capa
        explanation = (
            f"Charge {load:.1f} min <= capacite horizon {capa:.1f} min"
            if is_ok
            else f"Surcharge : {load:.1f} > {capa:.1f}"
        )
        check = CoherenceCheck(
            workstation_id=wid,
            metric="workstation_load",
            actual_value=load,
            limit_value=capa,
            is_ok=is_ok,
            explanation=explanation,
        )
        report.checks.append(check)
        if not is_ok:
            report.overall_ok = False

    # 2. Takt contractuel vs goulot
    _, max_unit_time = _bottleneck_unit_time(conn, candidate_ids)
    takt = ver.takt_target_min
    if takt is not None and max_unit_time > 0:
        is_ok = takt >= max_unit_time
        explanation = (
            f"Takt contractuel {takt:.2f} >= takt goulot {max_unit_time:.2f}"
            if is_ok
            else f"Takt contractuel {takt:.2f} < takt goulot {max_unit_time:.2f} : intenable"
        )
        check = CoherenceCheck(
            workstation_id=None,
            metric="takt_vs_bottleneck",
            actual_value=takt,
            limit_value=max_unit_time,
            is_ok=is_ok,
            explanation=explanation,
        )
        report.checks.append(check)
        if not is_ok:
            report.overall_ok = False

    # Persistance
    for c in report.checks:
        conn.execute(
            """
            INSERT INTO flux_coherence_checks
                (contract_id, version, workstation_id, metric,
                 actual_value, limit_value, is_ok, explanation)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                contract_id, version, c.workstation_id, c.metric,
                c.actual_value, c.limit_value, 1 if c.is_ok else 0, c.explanation,
            ),
        )

    conn.execute(
        "UPDATE flux_contract_versions SET is_coherent = ? "
        "WHERE contract_id = ? AND version = ?",
        (1 if report.overall_ok else 0, contract_id, version),
    )
    # Le statut du contrat ne reflète que la version courante
    if version == contract.current_version:
        new_status = "coherent" if report.overall_ok else "incoherent"
        conn.execute(
            "UPDATE flux_contracts SET status = ?, updated_at = datetime('now') "
            "WHERE contract_id = ?",
            (new_status, contract_id),
        )

    return report
