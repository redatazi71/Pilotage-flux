"""V13.K — Intégration zone négociable au runner comparatif.

Ce module fait le pont entre le simulateur (runner.py) et l'infrastructure
zone négociable enrichie (V13.H + V13.I + V13.J) :

  - Après la promotion des candidates en OFs (P1 ou P3 freeze),
    créer un demand_contract par SO couvert avec le dossier de
    faisabilité issu de V13.E.
  - Après création des demand_contracts, calculer les weekly_flux_contracts
    pour toutes les semaines qui ont au moins un demand.
  - À chaque jour de simulation, snapshot les twin_states pour chaque
    weekly actif.

Ces fonctions sont gates par le flag `enable_zone_negociable` (default 0
= off) pour ne pas impacter les études existantes.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

from pilotage_flux.flux.demand_contract import create_demand_contract
from pilotage_flux.flux.twin_state import snapshot_twin_state
from pilotage_flux.flux.weekly_contract import compute_weekly_flux_contract
from pilotage_flux.parameters import get_num


def is_zone_negociable_enabled(conn: sqlite3.Connection) -> bool:
    """Flag global `enable_zone_negociable` (default 0)."""
    val = get_num(
        conn, scope="global", scope_ref=None,
        name="enable_zone_negociable", default=0.0,
    )
    return bool(val and float(val) > 0.5)


def create_demand_contracts_for_promoted(
    conn: sqlite3.Connection,
    *,
    feasibility_by_candidate: dict[str, dict] | None = None,
) -> list[str]:
    """Crée un demand_contract par SO qui a au moins un candidate
    promoted et n'a pas encore de contrat.

    Chaque contrat est enrichi avec le dossier de faisabilité V13.E
    du candidate correspondant si disponible.

    Retourne la liste des contract_ids créés.
    """
    # Récupère les SOs candidates promoted sans contrat existant
    rows = conn.execute(
        """
        SELECT DISTINCT c.candidate_id, c.sales_order_id, c.article_id,
               c.quantity, so.due_date
        FROM candidate_orders c
        JOIN sales_orders so ON so.sales_order_id = c.sales_order_id
        WHERE c.status = 'promoted'
          AND c.candidate_id NOT IN (
              SELECT candidate_id FROM demand_contracts
              WHERE candidate_id IS NOT NULL
          )
        """
    ).fetchall()
    created: list[str] = []
    for r in rows:
        feas = (
            feasibility_by_candidate.get(r["candidate_id"])
            if feasibility_by_candidate else None
        )
        cid = create_demand_contract(
            conn,
            sales_order_id=r["sales_order_id"],
            article_id=r["article_id"],
            quantity=float(r["quantity"]),
            delivery_deadline=r["due_date"],
            candidate_id=r["candidate_id"],
            feasibility=feas,
        )
        created.append(cid)
    return created


def ensure_weekly_contracts_for_horizon(
    conn: sqlite3.Connection,
    *,
    horizon_start: str,
    horizon_days: int,
    target_saturation: float = 0.85,
) -> list[str]:
    """Calcule un weekly_flux_contract pour chaque semaine ISO ayant
    au moins un demand_contract sur l'horizon. Idempotent :
    ne recrée pas si déjà présent.

    Retourne la liste des weekly_ids créés (ou existants).
    """
    start = datetime.fromisoformat(horizon_start).date()
    end = start + timedelta(days=horizon_days)
    # Récupère toutes les delivery_deadlines de l'horizon
    rows = conn.execute(
        """
        SELECT DISTINCT delivery_deadline
        FROM demand_contracts
        WHERE date(delivery_deadline) BETWEEN date(?) AND date(?)
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    # Regroupe par (year_iso, week_iso) via isocalendar Python
    weeks: set[tuple[int, int]] = set()
    for r in rows:
        try:
            d = datetime.fromisoformat(r["delivery_deadline"]).date()
            y, w, _ = d.isocalendar()
            weeks.add((y, w))
        except ValueError:
            continue
    result: list[str] = []
    for y, w in sorted(weeks):
        # Vérifie si déjà présent
        existing = conn.execute(
            "SELECT weekly_id FROM weekly_flux_contracts "
            "WHERE year_iso = ? AND week_iso = ?",
            (y, w),
        ).fetchone()
        if existing:
            result.append(existing["weekly_id"])
            continue
        try:
            wid = compute_weekly_flux_contract(
                conn, year_iso=y, week_iso=w,
                target_saturation=target_saturation,
            )
            result.append(wid)
        except ValueError:
            continue
    return result


def snapshot_all_active_twins(
    conn: sqlite3.Connection,
    *,
    day: int,
    horizon_start: str,
    daily_wip: float | None = None,
) -> list[int]:
    """Prend un snapshot des twin_states pour tous les weekly_flux_contracts
    actifs (status IN 'draft', 'signed', 'active').

    Retourne la liste des twin_state_ids créés/mis à jour.
    """
    day_date = (
        datetime.fromisoformat(horizon_start).date()
        + timedelta(days=day)
    ).isoformat()
    rows = conn.execute(
        "SELECT weekly_id FROM weekly_flux_contracts "
        "WHERE status IN ('draft', 'signed', 'active')"
    ).fetchall()
    result: list[int] = []
    for r in rows:
        tid = snapshot_twin_state(
            conn, weekly_id=r["weekly_id"],
            snapshot_day=day, snapshot_date=day_date,
            daily_wip=daily_wip,
        )
        result.append(tid)
    return result


def wire_zone_negociable_after_promotion(
    conn: sqlite3.Connection,
    *,
    horizon_start: str,
    horizon_days: int,
    feasibility_by_candidate: dict[str, dict] | None = None,
    target_saturation: float = 0.85,
) -> dict:
    """One-shot appelé après une promotion (P1 ou P3 freeze) :
    crée les demand_contracts + weekly_flux_contracts.

    Renvoie {n_demand_created, n_weekly_created}.
    """
    if not is_zone_negociable_enabled(conn):
        return {"n_demand_created": 0, "n_weekly_created": 0}
    demands = create_demand_contracts_for_promoted(
        conn, feasibility_by_candidate=feasibility_by_candidate,
    )
    weeklies = ensure_weekly_contracts_for_horizon(
        conn, horizon_start=horizon_start,
        horizon_days=horizon_days,
        target_saturation=target_saturation,
    )
    return {
        "n_demand_created": len(demands),
        "n_weekly_created": len(weeklies),
    }
