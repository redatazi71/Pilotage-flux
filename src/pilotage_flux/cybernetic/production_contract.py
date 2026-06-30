"""Contrat de Production PC=(T, Ep, Er, C, O) au grain opération.

Composant Goldilocks #4 du cadrage v1.3 §3.10. Chaque opération
d'OF (`order_operations`) porte un Contrat de Production qui engage
le système sur 5 dimensions :

  - **T** : Temps cible (cycle minutes prévues = unit_time × quantité)
  - **Ep** : Engagement procédural (rendement qualité attendu)
  - **Er** : Engagement de résultats (quantité bonne livrée)
  - **C** : Coûts cible (€ = MOD horaire + matière BOM-amont)
  - **O** : Origine (référence vers SO/candidate/flux_contract)

Le PC est le **support contractuel** d'évaluation des sorties P3
(composant #5) et du moteur Delta (composant #6). Quand actual_*
est rempli post-MES, on calcule pour chaque dimension :

    abs(actual - target) / max(target, ε) <= tolerance_pct → OK

Si toutes les dimensions sont OK → `status = 'fulfilled'`.
Si au moins une est hors bande → `status = 'breached'` et
`breach_dimensions` liste les dimensions concernées en CSV ('T,Er').

API minimale :
  - build_pc_for_operation(conn, of_op_id, origin_kind, origin_ref,
        tolerances?) -> int            : crée un PC (renvoie pc_id)
  - build_pcs_for_of(conn, of_id, origin_kind, origin_ref) -> list[int]
        : crée les PCs pour toutes les opérations d'un OF
  - evaluate_pc(conn, pc_id) -> PCEvaluation : lit actuals + statue
  - get_pc(conn, pc_id) -> dict | None
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pilotage_flux.parameters import get_num


DEFAULT_TOLERANCE_TIME = 0.10      # ±10 % sur T
DEFAULT_TOLERANCE_QUALITY = 0.05   # ±5 pp sur Ep (taux)
DEFAULT_TOLERANCE_QUANTITY = 0.03  # ±3 % sur Er
DEFAULT_TOLERANCE_COST = 0.10      # ±10 % sur C

# Dimensions PC dans l'ordre canonique cadrage
PC_DIMENSIONS = ("T", "Ep", "Er", "C")
PC_ORIGIN_KINDS = ("sales_order", "candidate", "flux_contract")


@dataclass(frozen=True)
class PCTolerances:
    """Tolérances par dimension (fractions, ex. 0.10 = ±10 %)."""
    time: float = DEFAULT_TOLERANCE_TIME
    quality: float = DEFAULT_TOLERANCE_QUALITY
    quantity: float = DEFAULT_TOLERANCE_QUANTITY
    cost: float = DEFAULT_TOLERANCE_COST


@dataclass(frozen=True)
class PCEvaluation:
    """Résultat d'évaluation d'un PC post-MES."""
    pc_id: int
    status: str                   # 'open' | 'fulfilled' | 'breached'
    time_ok: bool | None          # None = pas d'actual
    quality_ok: bool | None
    quantity_ok: bool | None
    cost_ok: bool | None
    breach_dimensions: tuple[str, ...]

    @property
    def is_fulfilled(self) -> bool:
        return self.status == "fulfilled"

    @property
    def is_breached(self) -> bool:
        return self.status == "breached"


def _hourly_rate(
    conn: sqlite3.Connection, workstation_id: str,
) -> float:
    v = get_num(
        conn, scope="workstation", scope_ref=workstation_id,
        name="hourly_rate", default=0.0,
    )
    return float(v) if v is not None else 0.0


def _within_tolerance(
    actual: float, target: float, tol_frac: float,
) -> bool:
    """abs(actual - target) / max(target, ε) <= tol_frac.

    Si target = 0, on accepte actual = 0 et refuse sinon.
    """
    if target == 0.0:
        return actual == 0.0
    return abs(actual - target) / abs(target) <= tol_frac


def build_pc_for_operation(
    conn: sqlite3.Connection,
    of_op_id: int,
    *,
    origin_kind: str,
    origin_ref: str,
    tolerances: PCTolerances | None = None,
) -> int:
    """Construit un PC pour une opération.

    Lit `order_operations` pour récupérer le contexte (OF, WS,
    unit_time_min). Cible Er = OF.quantity ; cible T = unit_time_min
    × quantity ; cible C = T × hourly_rate(WS) / 60.

    Renvoie le pc_id créé. Lève ValueError si of_op_id introuvable
    ou si un PC existe déjà (UNIQUE constraint).
    """
    if origin_kind not in PC_ORIGIN_KINDS:
        raise ValueError(
            f"origin_kind inconnu : {origin_kind} "
            f"(attendu {PC_ORIGIN_KINDS})"
        )
    op = conn.execute(
        """
        SELECT op.of_op_id, op.of_id, op.workstation_id, op.unit_time_min,
               mo.quantity
        FROM order_operations op
        JOIN manufacturing_orders mo ON mo.of_id = op.of_id
        WHERE op.of_op_id = ?
        """,
        (of_op_id,),
    ).fetchone()
    if op is None:
        raise ValueError(f"of_op_id {of_op_id} introuvable")

    tol = tolerances or PCTolerances()
    quantity = float(op["quantity"])
    unit_time = float(op["unit_time_min"])
    target_time = unit_time * quantity
    rate = _hourly_rate(conn, op["workstation_id"])
    target_cost = target_time * rate / 60.0

    cursor = conn.execute(
        """
        INSERT INTO production_contracts (
            of_id, of_op_id,
            target_time_min, tolerance_pct_time,
            target_quality_rate, tolerance_pct_quality,
            target_qty_good, tolerance_pct_quantity,
            target_cost, tolerance_pct_cost,
            origin_kind, origin_ref
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            op["of_id"], of_op_id,
            target_time, tol.time,
            1.0, tol.quality,
            quantity, tol.quantity,
            target_cost, tol.cost,
            origin_kind, origin_ref,
        ),
    )
    return int(cursor.lastrowid)


def build_pcs_for_of(
    conn: sqlite3.Connection,
    of_id: str,
    *,
    origin_kind: str,
    origin_ref: str,
    tolerances: PCTolerances | None = None,
) -> list[int]:
    """Construit un PC pour chaque opération d'un OF.

    Renvoie la liste des pc_id créés, dans l'ordre sequence_idx.
    """
    rows = conn.execute(
        "SELECT of_op_id FROM order_operations "
        "WHERE of_id = ? ORDER BY sequence_idx",
        (of_id,),
    ).fetchall()
    pcs: list[int] = []
    for r in rows:
        pcs.append(
            build_pc_for_operation(
                conn, int(r["of_op_id"]),
                origin_kind=origin_kind, origin_ref=origin_ref,
                tolerances=tolerances,
            )
        )
    return pcs


def evaluate_pc(
    conn: sqlite3.Connection, pc_id: int,
) -> PCEvaluation:
    """Évalue un PC en lisant les actuals (déjà écrits par MES/closing).

    Si une dimension n'a pas d'actual, son flag = None (PC reste open
    si toutes les dimensions sont None ; en pratique on évalue après
    la fermeture de l'op via close_pc_from_op).
    """
    row = conn.execute(
        "SELECT * FROM production_contracts WHERE pc_id = ?",
        (pc_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"pc_id {pc_id} introuvable")

    def _flag(actual_key: str, target_key: str, tol_key: str) -> bool | None:
        if row[actual_key] is None:
            return None
        return _within_tolerance(
            float(row[actual_key]),
            float(row[target_key]),
            float(row[tol_key]),
        )

    t_ok = _flag("actual_time_min", "target_time_min", "tolerance_pct_time")
    q_ok = _flag(
        "actual_quality_rate", "target_quality_rate",
        "tolerance_pct_quality",
    )
    er_ok = _flag(
        "actual_qty_good", "target_qty_good", "tolerance_pct_quantity",
    )
    c_ok = _flag(
        "actual_cost", "target_cost", "tolerance_pct_cost",
    )

    flags = {"T": t_ok, "Ep": q_ok, "Er": er_ok, "C": c_ok}
    breaches = tuple(d for d in PC_DIMENSIONS if flags[d] is False)

    if all(f is None for f in flags.values()):
        status = "open"
    elif breaches:
        status = "breached"
    else:
        # Au moins un actual non-None et aucun breach → fulfilled
        # même si certaines dimensions sont encore None (acceptable :
        # un PC peut être fulfilled partiellement).
        status = "fulfilled"

    return PCEvaluation(
        pc_id=pc_id,
        status=status,
        time_ok=t_ok,
        quality_ok=q_ok,
        quantity_ok=er_ok,
        cost_ok=c_ok,
        breach_dimensions=breaches,
    )


def close_pc_from_op(
    conn: sqlite3.Connection, pc_id: int,
) -> PCEvaluation:
    """Remplit les actuals du PC depuis l'op + clôture le PC.

    Lit `order_operations` (actual_start/actual_end, qty_good,
    qty_scrap) pour calculer actual_time_min, actual_quality_rate,
    actual_qty_good et actual_cost. Puis appelle evaluate_pc et
    met à jour status + closed_at + breach_dimensions.
    """
    pc = conn.execute(
        "SELECT pc_id, of_op_id, target_time_min, target_qty_good "
        "FROM production_contracts WHERE pc_id = ?",
        (pc_id,),
    ).fetchone()
    if pc is None:
        raise ValueError(f"pc_id {pc_id} introuvable")

    op = conn.execute(
        """
        SELECT actual_start, actual_end, qty_good, qty_scrap,
               unit_time_min, workstation_id
        FROM order_operations WHERE of_op_id = ?
        """,
        (int(pc["of_op_id"]),),
    ).fetchone()
    if op is None:
        raise ValueError(f"order_operation pour PC {pc_id} introuvable")

    # actual_time_min : différence start/end si dispo, sinon fallback
    actual_time: float | None = None
    if op["actual_start"] and op["actual_end"]:
        try:
            from datetime import datetime
            ds = datetime.fromisoformat(op["actual_start"])
            de = datetime.fromisoformat(op["actual_end"])
            actual_time = max(0.0, (de - ds).total_seconds() / 60.0)
        except (ValueError, TypeError):
            actual_time = None

    qty_good = (
        float(op["qty_good"]) if op["qty_good"] is not None else None
    )
    qty_scrap = (
        float(op["qty_scrap"]) if op["qty_scrap"] is not None else 0.0
    )
    actual_quality: float | None = None
    if qty_good is not None:
        produced = qty_good + qty_scrap
        if produced > 0:
            actual_quality = qty_good / produced

    actual_cost: float | None = None
    if actual_time is not None:
        rate = _hourly_rate(conn, op["workstation_id"])
        actual_cost = actual_time * rate / 60.0

    conn.execute(
        """
        UPDATE production_contracts
        SET actual_time_min = ?,
            actual_quality_rate = ?,
            actual_qty_good = ?,
            actual_cost = ?
        WHERE pc_id = ?
        """,
        (actual_time, actual_quality, qty_good, actual_cost, pc_id),
    )

    eval_ = evaluate_pc(conn, pc_id)
    breach_csv = ",".join(eval_.breach_dimensions) or None
    conn.execute(
        """
        UPDATE production_contracts
        SET status = ?, breach_dimensions = ?,
            closed_at = datetime('now')
        WHERE pc_id = ?
        """,
        (eval_.status, breach_csv, pc_id),
    )
    return eval_


def get_pc(
    conn: sqlite3.Connection, pc_id: int,
) -> dict | None:
    """Renvoie la ligne PC sous forme de dict, ou None."""
    row = conn.execute(
        "SELECT * FROM production_contracts WHERE pc_id = ?",
        (pc_id,),
    ).fetchone()
    return dict(row) if row else None


def count_pcs_by_status(
    conn: sqlite3.Connection,
) -> dict[str, int]:
    """Distribution des PCs par statut (utile pour KPIs)."""
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM production_contracts "
        "GROUP BY status"
    ).fetchall()
    return {r["status"]: int(r["n"]) for r in rows}
