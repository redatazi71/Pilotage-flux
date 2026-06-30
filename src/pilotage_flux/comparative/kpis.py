"""KPIs comparatifs §19 du cadrage (L4.3).

Calculés depuis la DB SQLite + le RunResult d'un runner doctrinal.

KPIs mesurés :
  - lead_time_days_avg         : moyenne (close_day - created_day) sur les OF clôturés
  - lead_time_days_max         : max
  - wip_avg                    : moyenne du WIP journalier
  - of_closed                  : nb d'OF effectivement clôturés
  - of_total                   : nb d'OF créés
  - aps_recalculations         : nb de cycles APS (CBN + P1) exécutés sur le scénario
  - deviations_detected        : pour EVENT, nb d'écarts attendus/réels qualifiés
  - avg_time_deviation_minutes : pour EVENT, magnitude moyenne des écarts time_delta
  - actions_triggered          : pour EVENT, nb de tolerance_filter_decisions triggered
  - replan_local_actions       : nb correct_local + replan_local
  - replan_global_actions      : nb replan_global
  - causes_attached            : nb event_deviation_causes
  - quality_events             : nb d'événements qualité enregistrés
  - nervousness                : taux de nervosité = aps_recalculations / horizon_days
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pilotage_flux.costing import compute_run_cost_report, seed_default_unit_costs
from pilotage_flux.db import db_session

from pilotage_flux.comparative.runner import RunResult
from pilotage_flux.comparative.scenario import (
    DOCTRINE_EVENT,
    DOCTRINE_FLUX,
    DOCTRINE_OF_EVENT,
    Scenario,
)


@dataclass
class KpiSet:
    doctrine: str
    scenario_name: str
    lead_time_days_avg: float
    lead_time_days_max: int
    wip_avg: float
    of_total: int
    of_closed: int
    aps_recalculations: int
    deviations_detected: int
    avg_time_deviation_minutes: float | None
    actions_triggered: int
    replan_local_actions: int
    replan_global_actions: int
    causes_attached: int
    quality_events: int
    nervousness: float
    total_cost_eur: float = 0.0
    cost_per_of_eur: float = 0.0
    cost_scrap_eur: float = 0.0
    # Point 2 paper — disponibilité réelle (vs disponibilité OF-level)
    so_total: int = 0
    so_delivered: int = 0
    so_rejected: int = 0
    disponibility_so_level: float = 1.0
    # = so_delivered / so_total (1.0 = aucune SO refusée)
    # Option A QCDS — compliance quantité livrée vs demandée
    qty_demanded_total: float = 0.0
    qty_delivered_total: float = 0.0
    quantity_compliance: float = 1.0
    # = qty_delivered / qty_demanded (1.0 = livré exactement la quantité demandée)
    n_so_underdelivered: int = 0
    # SOs livrées avec quantité < demandée
    n_so_overdelivered: int = 0
    # SOs livrées avec quantité > demandée (effets d'arrondi lots)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _avg(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _count(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return 0
    key = row.keys()[0] if hasattr(row, "keys") else 0
    return int(row[key]) if row[key] is not None else 0


def compute_kpis(scenario: Scenario, result: RunResult) -> KpiSet:
    """Calcule les KPIs comparatifs à partir du RunResult et de la DB."""
    lead_times = [
        result.of_closed_day[of_id] - result.of_created_day.get(of_id, 0)
        for of_id in result.of_closed_day
    ]
    avg_lt = _avg([float(x) for x in lead_times])
    max_lt = max(lead_times) if lead_times else 0
    wips = [float(v) for v in result.daily_wip.values()]
    avg_wip = _avg(wips)
    nervousness = (
        result.aps_recalculations / float(scenario.horizon_days)
        if scenario.horizon_days > 0 else 0.0
    )

    avg_time_dev: float | None = None
    deviations_detected = 0
    actions_triggered = 0
    replan_local_actions = 0
    replan_global_actions = 0
    causes_attached = 0
    quality_events = 0

    total_cost_eur = 0.0
    cost_per_of_eur = 0.0
    cost_scrap_eur = 0.0

    with db_session(result.db_path) as conn:
        # Seed des prix unitaires (idempotent) pour valoriser le run
        seed_default_unit_costs(conn)
        cost_report = compute_run_cost_report(conn)
        total_cost_eur = round(cost_report.grand_total, 2)
        cost_per_of_eur = round(cost_report.cost_per_of, 2)
        cost_scrap_eur = round(cost_report.total_scrap, 2)

        qe_row = conn.execute(
            "SELECT COUNT(*) AS n FROM quality_events"
        ).fetchone()
        quality_events = int(qe_row["n"]) if qe_row else 0

        # Point 2 paper — comptage SOs total / livré / rejeté
        so_row = conn.execute(
            "SELECT "
            " COUNT(*) AS total, "
            " SUM(CASE WHEN rejected_at IS NOT NULL THEN 1 ELSE 0 END) AS rejected "
            "FROM sales_orders"
        ).fetchone()
        so_total = int(so_row["total"]) if so_row else 0
        so_rejected = int(so_row["rejected"]) if (
            so_row and so_row["rejected"] is not None
        ) else 0
        so_delivered = so_total - so_rejected
        disponibility_so_level = (
            so_delivered / so_total if so_total > 0 else 1.0
        )

        # Option A QCDS — quantity_compliance
        # Pour chaque SO non rejetée : compare qty demandée vs Σ qty_good
        # des OFs sur le même article via candidate_orders.
        qty_rows = conn.execute(
            """
            SELECT
                so.sales_order_id,
                so.quantity AS demanded,
                COALESCE(SUM(m.qty_good), 0) AS delivered
            FROM sales_orders so
            LEFT JOIN candidate_orders c
              ON c.sales_order_id = so.sales_order_id
            LEFT JOIN manufacturing_orders m
              ON m.candidate_id = c.candidate_id
              AND m.article_id = so.article_id
            WHERE so.rejected_at IS NULL
            GROUP BY so.sales_order_id, so.quantity
            """
        ).fetchall()
        qty_demanded_total = 0.0
        qty_delivered_total = 0.0
        n_so_underdelivered = 0
        n_so_overdelivered = 0
        for r in qty_rows:
            demanded = float(r["demanded"] or 0)
            delivered = float(r["delivered"] or 0)
            qty_demanded_total += demanded
            qty_delivered_total += delivered
            if delivered < demanded - 0.01:
                n_so_underdelivered += 1
            elif delivered > demanded + 0.01:
                n_so_overdelivered += 1
        quantity_compliance = (
            qty_delivered_total / qty_demanded_total
            if qty_demanded_total > 0 else 1.0
        )

        if result.doctrine in (DOCTRINE_EVENT, DOCTRINE_OF_EVENT):
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM event_deviations"
            ).fetchone()
            deviations_detected = int(row["n"]) if row else 0
            row = conn.execute(
                "SELECT AVG(ABS(delta_value)) AS m FROM event_deviations "
                "WHERE delta_value IS NOT NULL AND deviation_kind = 'time_delta'"
            ).fetchone()
            avg_time_dev = float(row["m"]) if row and row["m"] is not None else 0.0
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM tolerance_filter_decisions "
                "WHERE triggered_at IS NOT NULL"
            ).fetchone()
            actions_triggered = int(row["n"]) if row else 0
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM tolerance_filter_decisions "
                "WHERE action_level IN ('correct_local', 'replan_local')"
            ).fetchone()
            replan_local_actions = int(row["n"]) if row else 0
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM tolerance_filter_decisions "
                "WHERE action_level = 'replan_global'"
            ).fetchone()
            replan_global_actions = int(row["n"]) if row else 0
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM event_deviation_causes"
            ).fetchone()
            causes_attached = int(row["n"]) if row else 0

    return KpiSet(
        doctrine=result.doctrine,
        scenario_name=result.scenario_name,
        lead_time_days_avg=round(avg_lt, 2),
        lead_time_days_max=int(max_lt),
        wip_avg=round(avg_wip, 2),
        of_total=len(result.of_created_day),
        of_closed=len(result.of_closed_day),
        aps_recalculations=result.aps_recalculations,
        deviations_detected=deviations_detected,
        avg_time_deviation_minutes=(
            round(avg_time_dev, 2) if avg_time_dev is not None else None
        ),
        actions_triggered=actions_triggered,
        total_cost_eur=total_cost_eur,
        cost_per_of_eur=cost_per_of_eur,
        cost_scrap_eur=cost_scrap_eur,
        replan_local_actions=replan_local_actions,
        replan_global_actions=replan_global_actions,
        causes_attached=causes_attached,
        quality_events=quality_events,
        nervousness=round(nervousness, 3),
        so_total=so_total,
        so_delivered=so_delivered,
        so_rejected=so_rejected,
        disponibility_so_level=round(disponibility_so_level, 4),
        qty_demanded_total=round(qty_demanded_total, 2),
        qty_delivered_total=round(qty_delivered_total, 2),
        quantity_compliance=round(quantity_compliance, 4),
        n_so_underdelivered=n_so_underdelivered,
        n_so_overdelivered=n_so_overdelivered,
    )
