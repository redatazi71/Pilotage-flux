"""CPM scheduling (L11.1) — forward + backward pass sur le routing d'un OF.

Calcule pour chaque opération :
  - EST (earliest start time)
  - EFT (earliest finish time)
  - LST (latest start time)
  - LFT (latest finish time)
  - slack = LST − EST
  - is_critical (slack == 0)

Le makespan d'un OF = EFT de la dernière op.

Modèle :
  - Routing linéaire (séquence_idx 1 → N) : op[i+1].EST = op[i].EFT.
  - Durée d'une op = unit_time_min × OF.quantity / capacity_factor du poste.
  - Backward pass : LFT_last = EFT_last (makespan), LST_i = LFT_i − duration_i,
    LFT_{i−1} = LST_i.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from pilotage_flux.parameters import workstation_capacity_factor


@dataclass
class OperationNode:
    of_op_id: int
    sequence_idx: int
    workstation_id: str
    unit_time_min: float
    duration_min: float = 0.0
    est: float = 0.0
    eft: float = 0.0
    lst: float = 0.0
    lft: float = 0.0
    slack: float = 0.0
    is_critical: bool = False


@dataclass
class CpmReport:
    of_id: str
    quantity: float
    operations: list[OperationNode] = field(default_factory=list)
    makespan_min: float = 0.0
    critical_path: list[int] = field(default_factory=list)
    # of_op_ids des ops critiques (slack = 0) dans l'ordre sequence_idx

    @property
    def n_critical(self) -> int:
        return len(self.critical_path)


def _op_duration_min(
    conn: sqlite3.Connection,
    unit_time_min: float,
    quantity: float,
    workstation_id: str,
) -> float:
    """Durée effective d'une op = unit_time × qty / capacity_factor.

    Le capacity_factor < 1 augmente la durée (modélise le rendement effectif
    plus faible : un poste à 0.7 prend 1/0.7 = 1.43× le temps nominal).
    """
    capa = workstation_capacity_factor(conn, workstation_id)
    if capa <= 0:
        capa = 1.0
    return unit_time_min * quantity / capa


def compute_cpm_for_of(
    conn: sqlite3.Connection, of_id: str
) -> CpmReport:
    """Forward + backward pass pour un OF donné.

    Renvoie un CpmReport complet (toutes les ops avec EST/EFT/LST/LFT/slack).
    """
    of_row = conn.execute(
        "SELECT of_id, quantity FROM manufacturing_orders WHERE of_id = ?",
        (of_id,),
    ).fetchone()
    if of_row is None:
        raise ValueError(f"OF inconnu : {of_id}")
    quantity = float(of_row["quantity"])

    op_rows = conn.execute(
        """
        SELECT of_op_id, sequence_idx, workstation_id, unit_time_min
        FROM order_operations WHERE of_id = ?
        ORDER BY sequence_idx ASC
        """,
        (of_id,),
    ).fetchall()

    ops: list[OperationNode] = []
    for r in op_rows:
        duration = _op_duration_min(
            conn, float(r["unit_time_min"]), quantity, r["workstation_id"],
        )
        ops.append(OperationNode(
            of_op_id=int(r["of_op_id"]),
            sequence_idx=int(r["sequence_idx"]),
            workstation_id=r["workstation_id"],
            unit_time_min=float(r["unit_time_min"]),
            duration_min=duration,
        ))

    if not ops:
        return CpmReport(of_id=of_id, quantity=quantity)

    # Forward pass
    cursor_eft = 0.0
    for op in ops:
        op.est = cursor_eft
        op.eft = op.est + op.duration_min
        cursor_eft = op.eft

    makespan = ops[-1].eft

    # Backward pass
    cursor_lst = makespan
    for op in reversed(ops):
        op.lft = cursor_lst
        op.lst = op.lft - op.duration_min
        cursor_lst = op.lst

    # Slack + critical
    critical_path: list[int] = []
    for op in ops:
        op.slack = op.lst - op.est
        op.is_critical = abs(op.slack) < 1e-6
        if op.is_critical:
            critical_path.append(op.of_op_id)

    return CpmReport(
        of_id=of_id,
        quantity=quantity,
        operations=ops,
        makespan_min=makespan,
        critical_path=critical_path,
    )


def compute_makespan(conn: sqlite3.Connection, of_id: str) -> float:
    """Makespan d'un OF en minutes (EFT de la dernière op)."""
    return compute_cpm_for_of(conn, of_id).makespan_min


def list_critical_operations(
    conn: sqlite3.Connection, of_id: str
) -> list[OperationNode]:
    """Ops critiques (slack == 0) d'un OF."""
    report = compute_cpm_for_of(conn, of_id)
    return [op for op in report.operations if op.is_critical]
