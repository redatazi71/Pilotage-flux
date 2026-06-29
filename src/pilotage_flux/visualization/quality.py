"""Flux qualité (L6.2 — famille 3/5).

Vue des contrôles + non-conformités + rebuts/retouches par OF.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass
class QualityFlowItem:
    of_id: str
    article_id: str
    quantity: float
    qty_good: float
    qty_scrap: float
    yield_rate: float
    control_passes: int
    control_fails: int
    nc_opened: int
    nc_rework: int
    nc_scrap: int
    blocked: bool
    released: bool


@dataclass
class QualityFlowReport:
    items: list[QualityFlowItem] = field(default_factory=list)

    @property
    def total_nc(self) -> int:
        return sum(i.nc_opened for i in self.items)

    @property
    def overall_yield_rate(self) -> float:
        qg = sum(i.qty_good for i in self.items)
        qs = sum(i.qty_scrap for i in self.items)
        if qg + qs <= 0:
            return 1.0
        return qg / (qg + qs)


def _count_quality(
    conn: sqlite3.Connection, of_id: str, event_type: str
) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM quality_events "
        "WHERE of_id = ? AND event_type = ?",
        (of_id, event_type),
    ).fetchone()
    return int(row["n"]) if row else 0


def quality_flow_view(conn: sqlite3.Connection) -> QualityFlowReport:
    """Agrège les événements qualité par OF : contrôles, NC, rebuts."""
    report = QualityFlowReport()
    ofs = conn.execute(
        """
        SELECT of_id, article_id, quantity, qty_good, qty_scrap
        FROM manufacturing_orders
        WHERE status IN ('launched', 'in_progress', 'closed')
        ORDER BY of_id ASC
        """
    ).fetchall()
    for of in ofs:
        of_id = of["of_id"]
        qg = float(of["qty_good"] or 0.0)
        qs = float(of["qty_scrap"] or 0.0)
        yield_r = qg / (qg + qs) if (qg + qs) > 0 else 1.0
        blocked_row = conn.execute(
            "SELECT COUNT(*) AS n FROM quality_events "
            "WHERE of_id = ? AND event_type = 'block'",
            (of_id,),
        ).fetchone()
        released_row = conn.execute(
            "SELECT COUNT(*) AS n FROM quality_events "
            "WHERE of_id = ? AND event_type = 'release'",
            (of_id,),
        ).fetchone()
        report.items.append(QualityFlowItem(
            of_id=of_id,
            article_id=of["article_id"],
            quantity=float(of["quantity"]),
            qty_good=qg,
            qty_scrap=qs,
            yield_rate=yield_r,
            control_passes=_count_quality(conn, of_id, "control_pass"),
            control_fails=_count_quality(conn, of_id, "control_fail"),
            nc_opened=_count_quality(conn, of_id, "nc_opened"),
            nc_rework=_count_quality(conn, of_id, "nc_rework"),
            nc_scrap=_count_quality(conn, of_id, "nc_scrap"),
            blocked=int(blocked_row["n"]) > 0,
            released=int(released_row["n"]) > 0,
        ))
    return report
