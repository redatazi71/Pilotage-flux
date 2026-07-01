"""V13.J — Jumeau numérique 5 flux (twin state).

Capture l'état d'un contrat de flux hebdomadaire à un instant t
(jour de simulation) sur les 5 flux VSM :

    1. Physique      = WIP réel + OFs running + closed + livrés
    2. Informationnel = event sourcing (deviations, actions, causes)
    3. Décisionnel   = décisions replan / escalades
    4. Documentaire  = état contractuel (draft/signed/closed)
    5. Qualité       = scrap cumulé, NCs, yield moyen

Un snapshot est calculé à partir des tables existantes (event_deviations,
tolerance_filter_decisions, quality_events, etc.) et persisté dans
flux_twin_states. Sur horizon 60j → jusqu'à 60 snapshots par contrat.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class TwinState:
    weekly_id: str
    snapshot_day: int
    snapshot_date: str
    # Physique
    physical_wip_actual: float
    physical_ofs_running: int
    physical_ofs_closed: int
    physical_units_delivered: float
    # Informationnel
    info_deviations_detected: int
    info_actions_triggered: int
    info_causes_attached: int
    # Décisionnel
    decision_correct_local: int
    decision_replan_local: int
    decision_replan_global: int
    decision_escalate_human: int
    # Documentaire
    doc_contracts_draft: int
    doc_contracts_signed: int
    doc_contracts_closed: int
    # Qualité
    quality_scrap_cumul: float
    quality_nc_count: int
    quality_yield_rate: float | None


def _contracts_of_weekly(
    conn: sqlite3.Connection, weekly_id: str,
) -> list[str]:
    rows = conn.execute(
        "SELECT contract_id FROM weekly_flux_contract_lines "
        "WHERE weekly_id = ?",
        (weekly_id,),
    ).fetchall()
    return [r["contract_id"] for r in rows]


def _sos_of_weekly(
    conn: sqlite3.Connection, weekly_id: str,
) -> list[str]:
    """Renvoie les sales_order_id liés aux demand_contracts de ce hebdo."""
    rows = conn.execute(
        """
        SELECT DISTINCT dc.sales_order_id
        FROM weekly_flux_contract_lines wl
        JOIN demand_contracts dc ON dc.contract_id = wl.contract_id
        WHERE wl.weekly_id = ?
        """,
        (weekly_id,),
    ).fetchall()
    return [r["sales_order_id"] for r in rows]


def _ofs_of_weekly(
    conn: sqlite3.Connection, weekly_id: str,
) -> list[str]:
    """Renvoie les of_id liés aux SOs du hebdo via candidate → OF."""
    rows = conn.execute(
        """
        SELECT DISTINCT m.of_id
        FROM weekly_flux_contract_lines wl
        JOIN demand_contracts dc ON dc.contract_id = wl.contract_id
        LEFT JOIN candidate_orders c ON c.sales_order_id = dc.sales_order_id
        JOIN manufacturing_orders m ON m.candidate_id = c.candidate_id
        WHERE wl.weekly_id = ?
        """,
        (weekly_id,),
    ).fetchall()
    return [r["of_id"] for r in rows]


def snapshot_twin_state(
    conn: sqlite3.Connection,
    *,
    weekly_id: str,
    snapshot_day: int,
    snapshot_date: str,
    daily_wip: float | None = None,
) -> int:
    """Calcule et persiste un snapshot du jumeau à (weekly_id, day).

    `daily_wip` est passé de l'extérieur (RunResult.daily_wip) car les
    tables actuelles ne capturent pas le WIP journalier calculé. Si
    None, on utilise 0.

    Renvoie le twin_state_id créé (ou existant, si (weekly_id, day) déjà
    présent — mis à jour par REPLACE).
    """
    of_ids = _ofs_of_weekly(conn, weekly_id)
    sos = _sos_of_weekly(conn, weekly_id)
    contracts = _contracts_of_weekly(conn, weekly_id)

    # Flux 1 — Physique
    if of_ids:
        placeholders = ",".join("?" * len(of_ids))
        row = conn.execute(
            f"SELECT "
            f" SUM(CASE WHEN status IN ('launched','in_progress') "
            f"          THEN 1 ELSE 0 END) AS running, "
            f" SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) AS closed, "
            f" SUM(CASE WHEN status = 'closed' THEN qty_good ELSE 0 END) AS delivered "
            f"FROM manufacturing_orders WHERE of_id IN ({placeholders})",
            of_ids,
        ).fetchone()
        p_running = int(row["running"] or 0) if row else 0
        p_closed = int(row["closed"] or 0) if row else 0
        p_delivered = float(row["delivered"] or 0) if row else 0.0
    else:
        p_running = p_closed = 0
        p_delivered = 0.0

    # Flux 2 — Informationnel (event sourcing global du run)
    info = _count_events_info(conn)
    # Flux 3 — Décisionnel
    dec = _count_events_decision(conn)
    # Flux 4 — Documentaire
    doc = _count_doc_status(conn, contracts)
    # Flux 5 — Qualité
    qual = _count_quality(conn, of_ids)

    conn.execute(
        """
        INSERT OR REPLACE INTO flux_twin_states (
            weekly_id, snapshot_day, snapshot_date,
            physical_wip_actual, physical_ofs_running,
            physical_ofs_closed, physical_units_delivered,
            info_deviations_detected, info_actions_triggered,
            info_causes_attached,
            decision_correct_local, decision_replan_local,
            decision_replan_global, decision_escalate_human,
            doc_contracts_draft, doc_contracts_signed,
            doc_contracts_closed,
            quality_scrap_cumul, quality_nc_count, quality_yield_rate
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            weekly_id, snapshot_day, snapshot_date,
            daily_wip or 0.0, p_running, p_closed, p_delivered,
            info["dev"], info["act"], info["cau"],
            dec["cl"], dec["rl"], dec["rg"], dec["esc"],
            doc["draft"], doc["signed"], doc["closed"],
            qual["scrap"], qual["nc"], qual["yield"],
        ),
    )
    row = conn.execute(
        "SELECT twin_state_id FROM flux_twin_states "
        "WHERE weekly_id = ? AND snapshot_day = ?",
        (weekly_id, snapshot_day),
    ).fetchone()
    return int(row["twin_state_id"])


def _count_events_info(conn) -> dict:
    dev = conn.execute(
        "SELECT COUNT(*) AS n FROM event_deviations"
    ).fetchone()
    act = conn.execute(
        "SELECT COUNT(*) AS n FROM tolerance_filter_decisions "
        "WHERE triggered_at IS NOT NULL"
    ).fetchone()
    cau = conn.execute(
        "SELECT COUNT(*) AS n FROM event_deviation_causes"
    ).fetchone()
    return {
        "dev": int(dev["n"]) if dev else 0,
        "act": int(act["n"]) if act else 0,
        "cau": int(cau["n"]) if cau else 0,
    }


def _count_events_decision(conn) -> dict:
    cl = conn.execute(
        "SELECT COUNT(*) AS n FROM tolerance_filter_decisions "
        "WHERE action_level = 'correct_local'"
    ).fetchone()
    rl = conn.execute(
        "SELECT COUNT(*) AS n FROM tolerance_filter_decisions "
        "WHERE action_level = 'replan_local'"
    ).fetchone()
    rg = conn.execute(
        "SELECT COUNT(*) AS n FROM tolerance_filter_decisions "
        "WHERE action_level = 'replan_global'"
    ).fetchone()
    esc = conn.execute(
        "SELECT COUNT(*) AS n FROM tolerance_filter_decisions "
        "WHERE action_level = 'escalate'"
    ).fetchone()
    return {
        "cl": int(cl["n"]) if cl else 0,
        "rl": int(rl["n"]) if rl else 0,
        "rg": int(rg["n"]) if rg else 0,
        "esc": int(esc["n"]) if esc else 0,
    }


def _count_doc_status(conn, contract_ids: list[str]) -> dict:
    if not contract_ids:
        return {"draft": 0, "signed": 0, "closed": 0}
    placeholders = ",".join("?" * len(contract_ids))
    rows = conn.execute(
        f"SELECT flux_doc_status, COUNT(*) AS n FROM demand_contracts "
        f"WHERE contract_id IN ({placeholders}) GROUP BY flux_doc_status",
        contract_ids,
    ).fetchall()
    counts = {"draft": 0, "signed": 0, "closed": 0, "archived": 0}
    for r in rows:
        s = r["flux_doc_status"] or "draft"
        counts[s] = int(r["n"])
    return {
        "draft": counts.get("draft", 0),
        "signed": counts.get("signed", 0),
        "closed": counts.get("closed", 0),
    }


def _count_quality(conn, of_ids: list[str]) -> dict:
    if not of_ids:
        return {"scrap": 0.0, "nc": 0, "yield": None}
    placeholders = ",".join("?" * len(of_ids))
    row = conn.execute(
        f"SELECT "
        f" SUM(qty_scrap) AS scrap, "
        f" SUM(qty_good) AS good, "
        f" SUM(quantity) AS total "
        f"FROM manufacturing_orders WHERE of_id IN ({placeholders})",
        of_ids,
    ).fetchone()
    scrap = float(row["scrap"] or 0) if row else 0.0
    good = float(row["good"] or 0) if row else 0.0
    total = float(row["total"] or 0) if row else 0.0
    yield_rate = good / total if total > 0 else None
    nc = conn.execute(
        "SELECT COUNT(*) AS n FROM quality_events"
    ).fetchone()
    return {
        "scrap": scrap,
        "nc": int(nc["n"]) if nc else 0,
        "yield": yield_rate,
    }


def get_twin_state(
    conn: sqlite3.Connection, weekly_id: str, snapshot_day: int,
) -> TwinState | None:
    row = conn.execute(
        "SELECT * FROM flux_twin_states "
        "WHERE weekly_id = ? AND snapshot_day = ?",
        (weekly_id, snapshot_day),
    ).fetchone()
    if row is None:
        return None
    return _row_to_twin(row)


def get_twin_history(
    conn: sqlite3.Connection, weekly_id: str,
) -> list[TwinState]:
    rows = conn.execute(
        "SELECT * FROM flux_twin_states "
        "WHERE weekly_id = ? ORDER BY snapshot_day ASC",
        (weekly_id,),
    ).fetchall()
    return [_row_to_twin(r) for r in rows]


def _row_to_twin(row) -> TwinState:
    return TwinState(
        weekly_id=row["weekly_id"],
        snapshot_day=int(row["snapshot_day"]),
        snapshot_date=row["snapshot_date"],
        physical_wip_actual=float(row["physical_wip_actual"] or 0.0),
        physical_ofs_running=int(row["physical_ofs_running"] or 0),
        physical_ofs_closed=int(row["physical_ofs_closed"] or 0),
        physical_units_delivered=float(
            row["physical_units_delivered"] or 0.0
        ),
        info_deviations_detected=int(row["info_deviations_detected"] or 0),
        info_actions_triggered=int(row["info_actions_triggered"] or 0),
        info_causes_attached=int(row["info_causes_attached"] or 0),
        decision_correct_local=int(row["decision_correct_local"] or 0),
        decision_replan_local=int(row["decision_replan_local"] or 0),
        decision_replan_global=int(row["decision_replan_global"] or 0),
        decision_escalate_human=int(row["decision_escalate_human"] or 0),
        doc_contracts_draft=int(row["doc_contracts_draft"] or 0),
        doc_contracts_signed=int(row["doc_contracts_signed"] or 0),
        doc_contracts_closed=int(row["doc_contracts_closed"] or 0),
        quality_scrap_cumul=float(row["quality_scrap_cumul"] or 0.0),
        quality_nc_count=int(row["quality_nc_count"] or 0),
        quality_yield_rate=(
            float(row["quality_yield_rate"])
            if row["quality_yield_rate"] is not None else None
        ),
    )
