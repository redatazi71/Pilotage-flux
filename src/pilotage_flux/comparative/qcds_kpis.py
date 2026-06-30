"""KPIs unifiés QCDS — Qualité, Coût, Délai, Service + dynamiques.

Extrait depuis un RunResult + sa DB SQLite tous les KPIs nécessaires
pour comparer 6 pilotages sur 6 dimensions :

  - **OTIF**            : service (% SOs livrés à temps)
  - **Yield**           : qualité (qty_good / quantity sur OFs clôt.)
  - **Cost / good unit**: coût (€ par unité bonne livrée)
  - **WIP mean / p95**  : encours moyen / pic
  - **Lead time mean**  : délai moyen OF (jours du lancement à la clôture)
  - **Lateness mean**   : retard moyen SO (jours négatifs si avance)
  - **Robustesse**      : seuil de rupture OTIF par saturation
  - **Agilité**         : temps moyen de récupération WIP post-hazard

Tous les KPIs sont calculés post-run sur la DB du run (sans
modification du runner). API stable consommée par les scripts
d'étude.
"""

from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass

from pilotage_flux.comparative.bce_kpis_advanced import (
    compute_agilite,
    compute_robustesse,
)
from pilotage_flux.costing.engine import compute_run_cost_report


@dataclass(frozen=True)
class QcdsKpis:
    """KPIs unifiés QCDS pour un run."""
    otif: float
    yield_pct: float                      # 0..1
    cost_per_good_unit: float | None      # None si pas de données coût
    wip_mean: float
    wip_p95: float
    wip_sd: float
    lead_time_mean_days: float | None
    lateness_mean_days: float
    n_so_late: int
    n_so_total: int
    n_of_closed: int
    n_hazards_observed: int
    mean_recovery_days: float | None
    n_recoveries_observed: int

    def to_dict(self) -> dict:
        return {
            "otif": self.otif,
            "yield_pct": self.yield_pct,
            "cost_per_good_unit": self.cost_per_good_unit or 0.0,
            "wip_mean": self.wip_mean,
            "wip_p95": self.wip_p95,
            "wip_sd": self.wip_sd,
            "lead_time_mean_days": self.lead_time_mean_days or 0.0,
            "lateness_mean_days": self.lateness_mean_days,
            "n_so_late": self.n_so_late,
            "n_so_total": self.n_so_total,
            "n_of_closed": self.n_of_closed,
            "n_hazards_observed": self.n_hazards_observed,
            "mean_recovery_days": self.mean_recovery_days or 0.0,
            "n_recoveries_observed": self.n_recoveries_observed,
        }


def _otif(conn: sqlite3.Connection) -> tuple[float, int, int]:
    """OTIF = SOs non rejetés / total. Renvoie (otif, n_ok, n_total)."""
    row = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN status='delivered' OR rejected_at IS NULL "
        "         THEN 1 ELSE 0 END) AS ok "
        "FROM sales_orders"
    ).fetchone()
    if row is None or row["total"] == 0:
        return 1.0, 0, 0
    total = int(row["total"])
    ok = int(row["ok"] or 0)
    return ok / total, ok, total


def _yield_pct(conn: sqlite3.Connection) -> tuple[float, int]:
    """Rendement qualité = sum(qty_good) / sum(quantity) sur OFs clos."""
    row = conn.execute(
        "SELECT SUM(quantity) AS total_q, SUM(qty_good) AS total_good, "
        "COUNT(*) AS n "
        "FROM manufacturing_orders WHERE status = 'closed'"
    ).fetchone()
    if row is None or not row["total_q"]:
        return 1.0, 0
    total_q = float(row["total_q"] or 0)
    total_good = float(row["total_good"] or 0)
    if total_q == 0:
        return 1.0, int(row["n"] or 0)
    return total_good / total_q, int(row["n"] or 0)


def _wip_stats(
    daily_wip: dict[int, int],
) -> tuple[float, float, float]:
    """Renvoie (mean, p95, sd) sur les valeurs WIP."""
    if not daily_wip:
        return 0.0, 0.0, 0.0
    vals = sorted(daily_wip.values())
    n = len(vals)
    mean = sum(vals) / n
    p95_idx = max(0, int(0.95 * n) - 1)
    p95 = float(vals[p95_idx])
    if n < 2:
        sd = 0.0
    else:
        sd = statistics.stdev(vals)
    return mean, p95, sd


def _lead_time_mean(conn: sqlite3.Connection) -> float | None:
    """Lead time = jours entre actual_start de la 1ère op et
    actual_end de la dernière, agrégé sur OFs clôturés."""
    rows = conn.execute(
        """
        SELECT mo.of_id,
               MIN(op.actual_start) AS first_start,
               MAX(op.actual_end)   AS last_end
        FROM manufacturing_orders mo
        JOIN order_operations op ON op.of_id = mo.of_id
        WHERE mo.status = 'closed'
          AND op.actual_start IS NOT NULL
          AND op.actual_end IS NOT NULL
        GROUP BY mo.of_id
        """
    ).fetchall()
    if not rows:
        return None
    from datetime import datetime
    durations: list[float] = []
    for r in rows:
        try:
            s = datetime.fromisoformat(r["first_start"])
            e = datetime.fromisoformat(r["last_end"])
            durations.append((e - s).total_seconds() / 86400.0)
        except (ValueError, TypeError):
            continue
    if not durations:
        return None
    return sum(durations) / len(durations)


def _lateness_stats(conn: sqlite3.Connection) -> tuple[float, int]:
    """Retard moyen SO = max(0, actual_delivery - due_date). Renvoie
    (mean_days, n_late)."""
    rows = conn.execute(
        """
        SELECT so.sales_order_id, so.due_date,
               MAX(op.actual_end) AS actual_end
        FROM sales_orders so
        LEFT JOIN candidate_orders co ON co.sales_order_id = so.sales_order_id
        LEFT JOIN manufacturing_orders mo ON mo.candidate_id = co.candidate_id
        LEFT JOIN order_operations op ON op.of_id = mo.of_id
        WHERE so.rejected_at IS NULL
        GROUP BY so.sales_order_id
        """
    ).fetchall()
    if not rows:
        return 0.0, 0
    from datetime import datetime, date
    lateness: list[float] = []
    n_late = 0
    for r in rows:
        if not r["actual_end"] or not r["due_date"]:
            continue
        try:
            ae = datetime.fromisoformat(r["actual_end"])
            dd = datetime.fromisoformat(r["due_date"])
            # due_date est une date, on prend fin de journée
            if dd.hour == 0 and dd.minute == 0:
                dd = dd.replace(hour=23, minute=59)
            delta_days = (ae - dd).total_seconds() / 86400.0
            if delta_days > 0:
                n_late += 1
                lateness.append(delta_days)
        except (ValueError, TypeError):
            continue
    if not lateness:
        return 0.0, 0
    return sum(lateness) / len(lateness), n_late


def _cost_per_good_unit(conn: sqlite3.Connection) -> float | None:
    """Coût total / total quantité bonne livrée (OFs clôturés)."""
    try:
        report = compute_run_cost_report(conn, status_filter="closed")
    except Exception:
        return None
    total_cost = report.grand_total
    total_good = sum(
        bd.qty_good for bd in report.of_breakdowns if bd.qty_good
    )
    if total_good <= 0:
        return None
    return total_cost / total_good


def extract_qcds_kpis(
    conn: sqlite3.Connection,
    run_result,
    scenario,
) -> QcdsKpis:
    """Calcule tous les KPIs QCDS depuis la DB du run.

    `scenario` est utilisé pour récupérer hazard_days et calculer
    l'agilité.
    """
    otif, _, n_total = _otif(conn)
    yield_pct, n_closed = _yield_pct(conn)
    wip_mean, wip_p95, wip_sd = _wip_stats(run_result.daily_wip)
    lead_time = _lead_time_mean(conn)
    lateness_mean, n_late = _lateness_stats(conn)
    cost_per_unit = _cost_per_good_unit(conn)

    hazard_days = sorted({h.day for h in scenario.hazards})
    agilite = compute_agilite(run_result.daily_wip, hazard_days)

    return QcdsKpis(
        otif=otif,
        yield_pct=yield_pct,
        cost_per_good_unit=cost_per_unit,
        wip_mean=wip_mean,
        wip_p95=wip_p95,
        wip_sd=wip_sd,
        lead_time_mean_days=lead_time,
        lateness_mean_days=lateness_mean,
        n_so_late=n_late,
        n_so_total=n_total,
        n_of_closed=n_closed,
        n_hazards_observed=len(run_result.hazards_observed),
        mean_recovery_days=agilite.mean_recovery_days,
        n_recoveries_observed=agilite.n_recoveries_observed,
    )


def compute_robustesse_by_pilotage(
    runs: list[dict],
    *,
    pilotage_key: str = "doctrine",
    kpi_key: str = "otif",
    saturation_key: str = "saturation",
    kpi_threshold: float = 0.90,
) -> dict[str, float | None]:
    """À partir des résultats de runs, calcule la robustesse par
    pilotage en agrégeant sur seeds et implantations.

    Renvoie {pilotage: breaking_point_saturation_or_None}.
    """
    from collections import defaultdict
    by_pil_sat: dict[str, dict[float, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for r in runs:
        if r.get("status") != "ok":
            continue
        try:
            sat = float(r[saturation_key])
            kpi = float(r[kpi_key])
        except (KeyError, ValueError, TypeError):
            continue
        by_pil_sat[r[pilotage_key]][sat].append(kpi)

    out: dict[str, float | None] = {}
    for pil, by_sat in by_pil_sat.items():
        mean_by_sat = {
            sat: statistics.mean(vals) for sat, vals in by_sat.items()
        }
        res = compute_robustesse(
            mean_by_sat, kpi_threshold=kpi_threshold,
        )
        out[pil] = res.breaking_point_saturation
    return out
