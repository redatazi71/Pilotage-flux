"""Vues du flux physique : par poste et par OF.

Pas de nouvelle table : agregation depuis order_operations, manufacturing_orders,
mes_declarations et event_store.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass
class WorkstationView:
    workstation_id: str
    label: str
    sequence_idx: int
    pending: list[dict] = field(default_factory=list)
    running: list[dict] = field(default_factory=list)
    done: list[dict] = field(default_factory=list)

    @property
    def wip(self) -> int:
        """Work-in-progress : nombre d'operations actives sur ce poste."""
        return len(self.running)


def workstation_view(conn: sqlite3.Connection) -> list[WorkstationView]:
    """Vue par poste : operations pending / running / done."""
    workstations = conn.execute(
        "SELECT workstation_id, label, sequence_idx FROM workstations ORDER BY sequence_idx ASC"
    ).fetchall()

    views: list[WorkstationView] = []
    for w in workstations:
        wid = w["workstation_id"]
        ops = conn.execute(
            """
            SELECT oo.of_op_id, oo.of_id, oo.status, oo.sequence_idx,
                   oo.qty_good, oo.qty_scrap, mo.article_id, mo.quantity
            FROM order_operations AS oo
            JOIN manufacturing_orders AS mo ON mo.of_id = oo.of_id
            WHERE oo.workstation_id = ?
            ORDER BY oo.of_id ASC, oo.sequence_idx ASC
            """,
            (wid,),
        ).fetchall()
        view = WorkstationView(
            workstation_id=wid,
            label=w["label"],
            sequence_idx=int(w["sequence_idx"]),
        )
        for op in ops:
            payload = {
                "of_id": op["of_id"],
                "of_op_id": op["of_op_id"],
                "article": op["article_id"],
                "quantity": float(op["quantity"]),
                "qty_good": float(op["qty_good"] or 0.0),
                "qty_scrap": float(op["qty_scrap"] or 0.0),
            }
            status = op["status"]
            if status == "pending":
                view.pending.append(payload)
            elif status == "running":
                view.running.append(payload)
            elif status == "done":
                view.done.append(payload)
        views.append(view)
    return views


@dataclass
class OperationDetail:
    of_op_id: int
    sequence_idx: int
    workstation_id: str
    status: str
    qty_good: float
    qty_scrap: float
    actual_start: str | None
    actual_end: str | None
    declarations: list[dict] = field(default_factory=list)


@dataclass
class OFDetail:
    of_id: str
    article_id: str
    quantity: float
    status: str
    qty_good: float
    qty_scrap: float
    operations: list[OperationDetail] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)


def of_detail_view(conn: sqlite3.Connection, of_id: str) -> OFDetail | None:
    of_row = conn.execute(
        """
        SELECT of_id, article_id, quantity, status, qty_good, qty_scrap
        FROM manufacturing_orders WHERE of_id = ?
        """,
        (of_id,),
    ).fetchone()
    if of_row is None:
        return None

    detail = OFDetail(
        of_id=of_row["of_id"],
        article_id=of_row["article_id"],
        quantity=float(of_row["quantity"]),
        status=of_row["status"],
        qty_good=float(of_row["qty_good"] or 0.0),
        qty_scrap=float(of_row["qty_scrap"] or 0.0),
    )

    ops = conn.execute(
        """
        SELECT of_op_id, sequence_idx, workstation_id, status,
               qty_good, qty_scrap, actual_start, actual_end
        FROM order_operations WHERE of_id = ? ORDER BY sequence_idx ASC
        """,
        (of_id,),
    ).fetchall()

    for op in ops:
        decls = conn.execute(
            """
            SELECT declaration_id, kind, at_time, qty_good, qty_scrap, note
            FROM mes_declarations WHERE of_op_id = ?
            ORDER BY declaration_id ASC
            """,
            (op["of_op_id"],),
        ).fetchall()
        detail.operations.append(
            OperationDetail(
                of_op_id=int(op["of_op_id"]),
                sequence_idx=int(op["sequence_idx"]),
                workstation_id=op["workstation_id"],
                status=op["status"],
                qty_good=float(op["qty_good"] or 0.0),
                qty_scrap=float(op["qty_scrap"] or 0.0),
                actual_start=op["actual_start"],
                actual_end=op["actual_end"],
                declarations=[dict(d) for d in decls],
            )
        )

    events = conn.execute(
        """
        SELECT event_id, occurred_at, event_type, payload_json
        FROM event_store
        WHERE aggregate_type = 'manufacturing_order' AND aggregate_id = ?
        ORDER BY event_id ASC
        """,
        (of_id,),
    ).fetchall()
    detail.events = [dict(e) for e in events]
    return detail
