"""V13.I — Contrats de flux hebdomadaires.

Un contrat de flux hebdomadaire agrège les demand_contracts (V13.H)
dont la livraison tombe dans une semaine ISO donnée. Il porte les
**cibles agrégées** de la semaine :

    takt_target_min = capa_goulot_semaine / Σ(quantities)
    wip_target      = Σ(wip_predicted) — approximation Little agrégée
    rho_bottleneck  = Σ(charge_goulot) / capa_goulot_semaine

Doctrine (industrielle) :
- Granularité hebdo = standard ISA-95 Level 4 (S&OP mensuel, MPS hebdo)
- Sur horizon 60j = ~9 semaines, 120j = ~17 semaines
- Le takt et WIP hebdo pilotent le MES sur toute la semaine
- Reneg possible si rho > 0.90 (escalade humaine ou split)
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from pilotage_flux.parameters import workstation_capacity_factor


@dataclass(frozen=True)
class WeeklyFluxContract:
    weekly_id: str
    year_iso: int
    week_iso: int
    week_start_date: str
    total_quantity: float
    n_contracts: int
    bottleneck_ws: str | None
    takt_target_min: float | None
    wip_target: float | None
    rho_bottleneck: float | None
    feasible: bool
    capa_goulot_week: float | None
    charge_goulot_week: float | None
    status: str


def _iso_week_of(iso_date: str) -> tuple[int, int, str]:
    """Renvoie (year_iso, week_iso, monday_YYYY-MM-DD) pour une date."""
    d = datetime.fromisoformat(iso_date).date()
    y, w, _ = d.isocalendar()
    monday = date.fromisocalendar(y, w, 1).isoformat()
    return y, w, monday


def _daily_minutes(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT daily_minutes FROM calendars LIMIT 1"
    ).fetchone()
    if row and row["daily_minutes"]:
        return int(row["daily_minutes"])
    return 480


def compute_weekly_flux_contract(
    conn: sqlite3.Connection,
    *,
    year_iso: int,
    week_iso: int,
    target_saturation: float = 0.85,
    weekly_id: str | None = None,
) -> str:
    """Agrège tous les demand_contracts de la semaine et crée le
    contrat de flux hebdomadaire.

    Étapes :
      1. Sélectionne les demand_contracts dont delivery_deadline
         tombe dans (year_iso, week_iso).
      2. Identifie le goulot dominant de cette semaine.
      3. Calcule capa_goulot_semaine = daily_min × 5 × capa × target_sat.
      4. Calcule takt = capa / Σ_quantities.
      5. Somme WIP prédits (Little agrégée).
      6. rho_bottleneck = charge_goulot / capa_goulot_semaine.
      7. feasible = 1 si rho ≤ 1.0.

    Renvoie weekly_id.
    """
    # Monday of the ISO week
    monday = date.fromisocalendar(year_iso, week_iso, 1)
    sunday = monday + timedelta(days=6)
    contracts = conn.execute(
        """
        SELECT contract_id, quantity, bottleneck_ws,
               charge_bottleneck_min, wip_predicted
        FROM demand_contracts
        WHERE date(delivery_deadline) BETWEEN date(?) AND date(?)
        """,
        (monday.isoformat(), sunday.isoformat()),
    ).fetchall()
    if not contracts:
        raise ValueError(
            f"Aucun demand_contract dans la semaine {year_iso}-W{week_iso:02d}"
        )

    total_qty = sum(float(c["quantity"]) for c in contracts)
    n_contracts = len(contracts)
    # Bottleneck dominant du mix hebdo (majority vote pondéré par charge)
    ws_charges: dict[str, float] = {}
    for c in contracts:
        bws = c["bottleneck_ws"]
        if bws is None:
            continue
        chg = float(c["charge_bottleneck_min"] or 0.0)
        ws_charges[bws] = ws_charges.get(bws, 0.0) + chg
    bottleneck = (
        max(ws_charges, key=ws_charges.get) if ws_charges else None
    )
    charge_goulot = ws_charges.get(bottleneck, 0.0) if bottleneck else 0.0

    daily_min = _daily_minutes(conn)
    if bottleneck:
        capa = workstation_capacity_factor(conn, bottleneck)
        # 5 jours ouvrés × capa × target_sat = budget hebdo goulot
        capa_goulot_week = daily_min * 5 * capa * target_saturation
    else:
        capa_goulot_week = daily_min * 5 * target_saturation
    takt = (
        capa_goulot_week / total_qty
        if total_qty > 0 else None
    )
    wip_target = sum(
        float(c["wip_predicted"] or 0.0) for c in contracts
    )
    rho = (
        charge_goulot / capa_goulot_week
        if capa_goulot_week > 0 else 0.0
    )
    feasible = 1 if rho <= 1.0 else 0

    wid = weekly_id or f"WFC-{year_iso}{week_iso:02d}-{uuid.uuid4().hex[:6]}"
    conn.execute(
        """
        INSERT INTO weekly_flux_contracts (
            weekly_id, year_iso, week_iso, week_start_date,
            total_quantity, n_contracts, bottleneck_ws,
            takt_target_min, wip_target, rho_bottleneck,
            feasible, capa_goulot_week, charge_goulot_week,
            status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft')
        """,
        (wid, year_iso, week_iso, monday.isoformat(),
         total_qty, n_contracts, bottleneck,
         takt, wip_target, rho,
         feasible, capa_goulot_week, charge_goulot),
    )
    # Lignes du contrat hebdo
    for c in contracts:
        conn.execute(
            """INSERT OR IGNORE INTO weekly_flux_contract_lines
                (weekly_id, contract_id) VALUES (?, ?)""",
            (wid, c["contract_id"]),
        )
    return wid


def get_weekly_flux_contract(
    conn: sqlite3.Connection, weekly_id: str,
) -> WeeklyFluxContract | None:
    row = conn.execute(
        "SELECT * FROM weekly_flux_contracts WHERE weekly_id = ?",
        (weekly_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_weekly(row)


def get_weekly_contracts_for_period(
    conn: sqlite3.Connection, year_iso: int, week_iso: int,
) -> list[WeeklyFluxContract]:
    rows = conn.execute(
        "SELECT * FROM weekly_flux_contracts "
        "WHERE year_iso = ? AND week_iso = ?",
        (year_iso, week_iso),
    ).fetchall()
    return [_row_to_weekly(r) for r in rows]


def get_lines_of_weekly(
    conn: sqlite3.Connection, weekly_id: str,
) -> list[str]:
    rows = conn.execute(
        "SELECT contract_id FROM weekly_flux_contract_lines "
        "WHERE weekly_id = ?",
        (weekly_id,),
    ).fetchall()
    return [r["contract_id"] for r in rows]


def sign_weekly_contract(conn, weekly_id: str) -> None:
    conn.execute(
        """UPDATE weekly_flux_contracts
           SET status = 'signed', signed_at = datetime('now')
           WHERE weekly_id = ?""",
        (weekly_id,),
    )


def close_weekly_contract(conn, weekly_id: str) -> None:
    conn.execute(
        """UPDATE weekly_flux_contracts
           SET status = 'closed', closed_at = datetime('now')
           WHERE weekly_id = ?""",
        (weekly_id,),
    )


def _row_to_weekly(row) -> WeeklyFluxContract:
    return WeeklyFluxContract(
        weekly_id=row["weekly_id"],
        year_iso=int(row["year_iso"]),
        week_iso=int(row["week_iso"]),
        week_start_date=row["week_start_date"],
        total_quantity=float(row["total_quantity"]),
        n_contracts=int(row["n_contracts"]),
        bottleneck_ws=row["bottleneck_ws"],
        takt_target_min=(
            float(row["takt_target_min"])
            if row["takt_target_min"] is not None else None
        ),
        wip_target=(
            float(row["wip_target"])
            if row["wip_target"] is not None else None
        ),
        rho_bottleneck=(
            float(row["rho_bottleneck"])
            if row["rho_bottleneck"] is not None else None
        ),
        feasible=bool(row["feasible"]),
        capa_goulot_week=(
            float(row["capa_goulot_week"])
            if row["capa_goulot_week"] is not None else None
        ),
        charge_goulot_week=(
            float(row["charge_goulot_week"])
            if row["charge_goulot_week"] is not None else None
        ),
        status=row["status"],
    )
