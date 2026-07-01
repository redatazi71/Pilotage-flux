"""V13.H — Contrats de production (zone négociable enrichie).

Un contrat de production matérialise la doctrine QCDS pour une SO :
    SO (demande) + candidate (planning brut) → contrat enrichi avec :
    - Cibles doctrinales : takt cible (min/unité), WIP cible (unités),
      buffer temporel avant goulot
    - Dossier de faisabilité : charge par WS (total et goulot), capa
      requise, WIP prédit (Little), ρ_bottleneck
    - Appro : statut ok/partial/missing (stock + PO)
    - État des 5 flux (jumeau numérique) : physique, info, décisionnel,
      documentaire, qualité

Créé au moment de la promotion candidate → OF (P3 freeze). Persiste
après clôture pour audit doctrinal (takt tenu vs cible, WIP écart, etc.).

Le contrat de production est **la brique atomique** de la zone
négociable. Ils sont ensuite regroupés en contrats de flux hebdomadaires
(V13.I) qui portent les cibles aggrégées.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class DemandContract:
    """Représentation typée d'un contrat de production."""

    contract_id: str
    sales_order_id: str
    article_id: str
    quantity: float
    delivery_deadline: str
    takt_target_min: float | None
    wip_target: float | None
    bottleneck_ws: str | None
    buffer_days: int
    charge_total_min: float | None
    charge_bottleneck_min: float | None
    capa_needed_min: float | None
    wip_predicted: float | None
    rho_bottleneck: float | None
    feasible: bool
    appro_status: str | None
    flux_physical_status: str | None
    flux_info_ready: bool
    flux_decision_status: str | None
    flux_doc_status: str | None
    flux_quality_status: str | None
    scheduled_start_day: int | None
    scheduled_end_day: int | None


def create_demand_contract(
    conn: sqlite3.Connection,
    *,
    sales_order_id: str,
    article_id: str,
    quantity: float,
    delivery_deadline: str,
    candidate_id: str | None = None,
    feasibility: dict | None = None,
    appro_status: str = "ok",
    contract_id: str | None = None,
) -> str:
    """Crée un contrat de production et le persiste.

    `feasibility` = dict issu de `_compute_toc_aware_offsets` (V13.E) :
        bottleneck_ws, goulot_slot_day, launch_day, buffer_days,
        charge_total_min, takt_min_per_unit_target, wip_predicted,
        rho_bottleneck_run, feasible, goulot_load_min

    Si `feasibility` est None, on crée le contrat minimal (sans cibles
    doctrinales, feasible=1 par défaut).

    `appro_status` = 'ok' | 'partial' | 'missing' (à calculer par
    `check_appro_status` séparément).

    Renvoie contract_id.
    """
    cid = contract_id or f"PC-{uuid.uuid4().hex[:12]}"
    feas = feasibility or {}
    charge_total = feas.get("charge_total_min")
    charge_goulot = feas.get("goulot_load_min")
    takt = feas.get("takt_min_per_unit_target")
    wip_pred = feas.get("wip_predicted")
    rho = feas.get("rho_bottleneck_run")
    bws = feas.get("bottleneck_ws")
    bd = feas.get("buffer_days", 2)
    is_feasible = int(bool(feas.get("feasible", 1)))
    # WIP target = throughput × cycle (Little). Ici WIP_predicted sert
    # de cible aussi (ce que l'on prédit doit être ce que l'on vise).
    wip_target = wip_pred
    # capa_needed = charge_bottleneck × (1 + queueing_margin) — approx :
    # on prend charge_bottleneck directement (pas de queueing modélisé
    # au niveau contrat pour l'instant).
    capa_needed = charge_goulot

    conn.execute(
        """
        INSERT INTO demand_contracts (
            contract_id, sales_order_id, candidate_id, article_id,
            quantity, delivery_deadline,
            takt_target_min, wip_target, bottleneck_ws, buffer_days,
            charge_total_min, charge_bottleneck_min, capa_needed_min,
            wip_predicted, rho_bottleneck, feasible,
            appro_status,
            flux_physical_status, flux_info_ready,
            flux_decision_status, flux_doc_status, flux_quality_status
        ) VALUES (?, ?, ?, ?, ?, ?,
                   ?, ?, ?, ?,
                   ?, ?, ?,
                   ?, ?, ?,
                   ?,
                   ?, ?,
                   ?, ?, ?)
        """,
        (
            cid, sales_order_id, candidate_id, article_id,
            quantity, delivery_deadline,
            takt, wip_target, bws, bd,
            charge_total, charge_goulot, capa_needed,
            wip_pred, rho, is_feasible,
            appro_status,
            "planned", 0,
            "auto", "draft", "planned",
        ),
    )
    return cid


def get_demand_contract(
    conn: sqlite3.Connection, contract_id: str,
) -> DemandContract | None:
    """Renvoie un contrat par son id, ou None si absent."""
    row = conn.execute(
        "SELECT * FROM demand_contracts WHERE contract_id = ?",
        (contract_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_contract(row)


def get_demand_contracts_by_so(
    conn: sqlite3.Connection, sales_order_id: str,
) -> list[DemandContract]:
    """Renvoie tous les contrats de production d'une SO."""
    rows = conn.execute(
        "SELECT * FROM demand_contracts "
        "WHERE sales_order_id = ? ORDER BY created_at ASC",
        (sales_order_id,),
    ).fetchall()
    return [_row_to_contract(r) for r in rows]


def get_infeasible_contracts(
    conn: sqlite3.Connection,
) -> list[DemandContract]:
    """Renvoie les contrats marqués comme infeasible (nécessitent
    escalade ou renégociation)."""
    rows = conn.execute(
        "SELECT * FROM demand_contracts WHERE feasible = 0"
    ).fetchall()
    return [_row_to_contract(r) for r in rows]


def sign_contract(
    conn: sqlite3.Connection, contract_id: str,
) -> None:
    """Signe le contrat (P3 freeze) : passe flux_doc_status à 'signed'
    et enregistre signed_at."""
    conn.execute(
        """UPDATE demand_contracts
           SET flux_doc_status = 'signed',
               signed_at = datetime('now')
           WHERE contract_id = ?""",
        (contract_id,),
    )


def close_contract(
    conn: sqlite3.Connection, contract_id: str,
) -> None:
    """Clôt le contrat (OF terminé) : passe flux_physical_status à
    'closed' et enregistre closed_at."""
    conn.execute(
        """UPDATE demand_contracts
           SET flux_physical_status = 'closed',
               closed_at = datetime('now')
           WHERE contract_id = ?""",
        (contract_id,),
    )


def check_appro_status(
    conn: sqlite3.Connection, contract_id: str,
) -> str:
    """Évalue l'état d'appro pour ce contrat : ok / partial / missing.

    Approche simplifiée : pour chaque composant BOM de l'article,
    vérifie si stock + PO_pending suffit à couvrir qty × BOM_qty.
    """
    contract = get_demand_contract(conn, contract_id)
    if contract is None:
        return "missing"
    # Récupère composants BOM
    bom = conn.execute(
        """SELECT child_article, quantity FROM bom_lines
           WHERE parent_article = ?""",
        (contract.article_id,),
    ).fetchall()
    if not bom:
        return "ok"  # article sans BOM → rien à approvisionner
    missing_count = 0
    partial_count = 0
    for r in bom:
        child = r["child_article"]
        needed = float(r["quantity"]) * contract.quantity
        # Stock courant
        stock_row = conn.execute(
            "SELECT qty_available FROM stocks WHERE article_id = ?",
            (child,),
        ).fetchone()
        stock = float(stock_row["qty_available"]) if stock_row else 0.0
        # POs pending (qty non encore reçue) pour ce composant
        po_row = conn.execute(
            """SELECT COALESCE(SUM(qty_ordered - qty_received), 0) AS q
               FROM purchase_orders
               WHERE article_id = ?""",
            (child,),
        ).fetchone()
        po_pending = float(po_row["q"]) if po_row else 0.0
        available = stock + po_pending
        if available < needed * 0.5:
            missing_count += 1
        elif available < needed:
            partial_count += 1
    if missing_count > 0:
        return "missing"
    if partial_count > 0:
        return "partial"
    return "ok"


def _row_to_contract(row) -> DemandContract:
    return DemandContract(
        contract_id=row["contract_id"],
        sales_order_id=row["sales_order_id"],
        article_id=row["article_id"],
        quantity=float(row["quantity"]),
        delivery_deadline=row["delivery_deadline"],
        takt_target_min=(
            float(row["takt_target_min"])
            if row["takt_target_min"] is not None else None
        ),
        wip_target=(
            float(row["wip_target"])
            if row["wip_target"] is not None else None
        ),
        bottleneck_ws=row["bottleneck_ws"],
        buffer_days=int(row["buffer_days"] or 2),
        charge_total_min=(
            float(row["charge_total_min"])
            if row["charge_total_min"] is not None else None
        ),
        charge_bottleneck_min=(
            float(row["charge_bottleneck_min"])
            if row["charge_bottleneck_min"] is not None else None
        ),
        capa_needed_min=(
            float(row["capa_needed_min"])
            if row["capa_needed_min"] is not None else None
        ),
        wip_predicted=(
            float(row["wip_predicted"])
            if row["wip_predicted"] is not None else None
        ),
        rho_bottleneck=(
            float(row["rho_bottleneck"])
            if row["rho_bottleneck"] is not None else None
        ),
        feasible=bool(row["feasible"]),
        appro_status=row["appro_status"],
        flux_physical_status=row["flux_physical_status"],
        flux_info_ready=bool(row["flux_info_ready"]),
        flux_decision_status=row["flux_decision_status"],
        flux_doc_status=row["flux_doc_status"],
        flux_quality_status=row["flux_quality_status"],
        scheduled_start_day=(
            int(row["scheduled_start_day"])
            if row["scheduled_start_day"] is not None else None
        ),
        scheduled_end_day=(
            int(row["scheduled_end_day"])
            if row["scheduled_end_day"] is not None else None
        ),
    )
