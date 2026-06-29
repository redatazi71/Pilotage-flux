"""Runners doctrinaux : exécution d'un scénario sous OF / FLUX / EVENT (L4.2).

Trois fonctions publiques :
  run_of_doctrine(scenario, db_path)
  run_flux_doctrine(scenario, db_path)
  run_event_doctrine(scenario, db_path)

Et un wrapper de dispatch :
  run_doctrine(scenario, doctrine, db_path)

Chaque runner exécute le scénario complet (commandes + aléas) selon une
doctrine et renvoie un RunResult avec les artefacts mesurables (jours de
clôture par OF, ré-évaluations APS, etc.).

Horloge logique : pour produire des écarts mesurables côté V3, chaque appel
MES est suivi d'un `_stamp_logical_day` qui réécrit `event_store.occurred_at`,
`order_operations.actual_start/actual_end` et `manufacturing_orders.actual_start/actual_end`
au timestamp logique (horizon_start + day en jours). Cela préserve les
contrats des modules MES tout en donnant des écarts attendu↔réel non nuls.
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pilotage_flux.aps import compute_candidates
from pilotage_flux.db import db_session, init_schema
from pilotage_flux.events_v3 import (
    attach_causes_to_deviation,
    capture_recipe,
    evaluate_all_open_deviations,
    generate_expected_from_batch,
    list_deviations,
    match_actuals_to_expected,
)
from pilotage_flux.flux import (
    compute_coherence,
    compute_smoothing,
    create_contract,
)
from pilotage_flux.gates import (
    run_p1_promotion,
    run_p2_on_libre_zone,
    run_p3_freeze,
)
from pilotage_flux.importers import import_referentials
from pilotage_flux.mes import close_of, finish_operation, launch_of, start_operation
from pilotage_flux.quality import open_nc, scrap_nc
from pilotage_flux.risk_debt import extinguish_risk_debt, list_risk_debts
from pilotage_flux.stocks_purchasing import (
    create_purchase,
    receive_purchase,
    set_stock,
)

from pilotage_flux.comparative.scenario import (
    DOCTRINE_EVENT,
    DOCTRINE_FLUX,
    DOCTRINE_OF,
    DOCTRINES,
    HAZARD_BREAKDOWN,
    HAZARD_PO_DELAY,
    HAZARD_QUALITY_NC,
    HAZARD_URGENT_ORDER,
    Scenario,
)


DEFAULT_FIXTURES_DIR = Path("data/fixtures_v1")


@dataclass
class RunResult:
    """Résultat d'un run doctrinal sur un scénario."""

    doctrine: str
    scenario_name: str
    db_path: Path
    seed: int
    of_created_day: dict[str, int] = field(default_factory=dict)
    of_closed_day: dict[str, int] = field(default_factory=dict)
    of_quantities: dict[str, float] = field(default_factory=dict)
    of_qty_good: dict[str, float] = field(default_factory=dict)
    of_qty_scrap: dict[str, float] = field(default_factory=dict)
    daily_wip: dict[int, int] = field(default_factory=dict)
    aps_recalculations: int = 0
    hazards_observed: list[dict[str, Any]] = field(default_factory=list)
    batch_id: str | None = None
    notes: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------
# Helpers d'horloge logique
# ----------------------------------------------------------------------


def _day_to_iso(horizon_start: str, day: int) -> str:
    """Convertit un jour logique en ISO datetime YYYY-MM-DD HH:MM:SS.

    L'heure est calée pour que jour D = horizon_start + D jours à 08:00 UTC.
    On échelonne 8h00, 12h00, 16h00 selon l'ordre d'événement dans la
    journée — handled par le runner qui appelle _stamp_logical_event.
    """
    base = datetime.fromisoformat(horizon_start)
    if base.hour == 0 and base.minute == 0:
        base = base.replace(hour=8)
    return (base + timedelta(days=day)).strftime("%Y-%m-%d %H:%M:%S")


def _stamp_event_at_day(
    conn: sqlite3.Connection,
    event_id: int,
    horizon_start: str,
    day: int,
    hour_offset_minutes: int = 0,
) -> None:
    """Réécrit event_store.occurred_at à un timestamp logique."""
    base = datetime.fromisoformat(horizon_start)
    if base.hour == 0 and base.minute == 0:
        base = base.replace(hour=8)
    ts = (base + timedelta(days=day, minutes=hour_offset_minutes)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    conn.execute(
        "UPDATE event_store SET occurred_at = ? WHERE event_id = ?",
        (ts, event_id),
    )


def _stamp_op_actual(
    conn: sqlite3.Connection,
    of_op_id: int,
    horizon_start: str,
    *,
    start_day: int | None = None,
    end_day: int | None = None,
    minutes_offset_start: int = 0,
    minutes_offset_end: int = 60,
) -> None:
    """Réécrit actual_start/actual_end de l'op au timestamp logique."""
    base = datetime.fromisoformat(horizon_start)
    if base.hour == 0 and base.minute == 0:
        base = base.replace(hour=8)
    if start_day is not None:
        ts = (base + timedelta(days=start_day, minutes=minutes_offset_start)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        conn.execute(
            "UPDATE order_operations SET actual_start = ? WHERE of_op_id = ?",
            (ts, of_op_id),
        )
    if end_day is not None:
        ts = (base + timedelta(days=end_day, minutes=minutes_offset_end)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        conn.execute(
            "UPDATE order_operations SET actual_end = ? WHERE of_op_id = ?",
            (ts, of_op_id),
        )


def _stamp_of_actual(
    conn: sqlite3.Connection,
    of_id: str,
    horizon_start: str,
    *,
    start_day: int | None = None,
    end_day: int | None = None,
) -> None:
    base = datetime.fromisoformat(horizon_start)
    if base.hour == 0 and base.minute == 0:
        base = base.replace(hour=8)
    if start_day is not None:
        ts = (base + timedelta(days=start_day)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "UPDATE manufacturing_orders SET actual_start = ? WHERE of_id = ?",
            (ts, of_id),
        )
    if end_day is not None:
        ts = (base + timedelta(days=end_day, hours=10)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        conn.execute(
            "UPDATE manufacturing_orders SET actual_end = ? WHERE of_id = ?",
            (ts, of_id),
        )


# ----------------------------------------------------------------------
# Initialisation commune
# ----------------------------------------------------------------------


def _import_sales_orders(
    conn: sqlite3.Connection, scenario: Scenario
) -> None:
    """Insère les SO du scénario en remplaçant ceux des fixtures."""
    conn.execute("DELETE FROM sales_orders")
    for so in scenario.initial_sales_orders:
        conn.execute(
            """
            INSERT INTO sales_orders (sales_order_id, article_id, quantity, due_date)
            VALUES (?, ?, ?, ?)
            """,
            (so["sales_order_id"], so["article_id"], so["quantity"], so["due_date"]),
        )


def _setup_stocks_and_pos(
    conn: sqlite3.Connection, scenario: Scenario
) -> None:
    for article_id, qty in scenario.initial_stocks.items():
        set_stock(conn, article_id, qty)
    for po in scenario.initial_purchase_orders:
        expected_at = _day_to_iso(scenario.horizon_start, int(po["expected_day"]))
        purchase = create_purchase(
            conn,
            article_id=po["article_id"],
            qty_ordered=float(po["qty"]),
            expected_at=expected_at,
        )
        # Renomme le PO id pour qu'il corresponde au scénario (PO-0001, etc.)
        if purchase.po_id != po["po_id"]:
            conn.execute(
                "UPDATE purchase_orders SET po_id = ? WHERE po_id = ?",
                (po["po_id"], purchase.po_id),
            )


def _bootstrap_db(
    scenario: Scenario, db_path: Path, fixtures_dir: Path
) -> None:
    """Crée la DB, importe les référentiels et applique l'état initial."""
    init_schema(db_path, drop_existing=True)
    with db_session(db_path) as conn:
        import_referentials(conn, fixtures_dir)
        _import_sales_orders(conn, scenario)
        _setup_stocks_and_pos(conn, scenario)


# ----------------------------------------------------------------------
# Exécution d'une opération unique avec horloge logique
# ----------------------------------------------------------------------


def _execute_op(
    conn: sqlite3.Connection,
    of_op_id: int,
    *,
    horizon_start: str,
    day: int,
    qty_good: float,
    qty_scrap: float,
    actor: str,
) -> tuple[int, int]:
    """Lance start + finish d'une opération et tamponne au jour logique.

    Renvoie (start_event_id, finish_event_id).
    """
    start_decl = start_operation(conn, of_op_id, actor=actor)
    finish_decl = finish_operation(
        conn, of_op_id, qty_good=qty_good, qty_scrap=qty_scrap, actor=actor
    )
    # Réécriture des timestamps pour matcher l'horloge logique du scénario
    _stamp_event_at_day(conn, start_decl.event_id, horizon_start, day, 0)
    _stamp_event_at_day(conn, finish_decl.event_id, horizon_start, day, 480)
    _stamp_op_actual(
        conn, of_op_id, horizon_start, start_day=day, end_day=day,
        minutes_offset_start=0, minutes_offset_end=480,
    )
    return start_decl.event_id, finish_decl.event_id


def _close_of_at_day(
    conn: sqlite3.Connection,
    of_id: str,
    *,
    horizon_start: str,
    day: int,
    actor: str,
) -> int:
    result = close_of(conn, of_id, actor=actor)
    _stamp_event_at_day(conn, result.event_id, horizon_start, day, 600)
    _stamp_of_actual(conn, of_id, horizon_start, end_day=day)
    return result.event_id


def _launch_of_at_day(
    conn: sqlite3.Connection,
    of_id: str,
    *,
    horizon_start: str,
    day: int,
    actor: str,
) -> int:
    result = launch_of(conn, of_id, actor=actor)
    _stamp_event_at_day(conn, result.event_id, horizon_start, day, 0)
    _stamp_of_actual(conn, of_id, horizon_start, start_day=day)
    return result.event_id


# ----------------------------------------------------------------------
# Application des aléas
# ----------------------------------------------------------------------


@dataclass
class HazardState:
    """État courant des aléas pour le runner."""

    breakdown_ws: dict[str, int] = field(default_factory=dict)
    # workstation_id -> nb jours restants de slowdown
    breakdown_factor: dict[str, float] = field(default_factory=dict)
    quality_nc_applied: bool = False
    urgent_order_id: str | None = None
    po_delays: dict[str, int] = field(default_factory=dict)


def _apply_hazard(
    conn: sqlite3.Connection,
    scenario: Scenario,
    hazard,
    state: HazardState,
    result: RunResult,
    doctrine: str,
) -> None:
    kind = hazard.kind
    payload = hazard.payload
    if kind == HAZARD_BREAKDOWN:
        ws = payload["workstation_id"]
        state.breakdown_ws[ws] = int(payload.get("duration_days", 1))
        state.breakdown_factor[ws] = float(payload.get("slowdown_factor", 1.5))
        result.hazards_observed.append(
            {"day": hazard.day, "kind": kind, "workstation_id": ws,
             "slowdown_factor": state.breakdown_factor[ws]}
        )
    elif kind == HAZARD_QUALITY_NC:
        article = payload["article_id"]
        qty_scrap = float(payload["qty_scrap"])
        # Trouve un OF en cours sur cet article et déclenche NC + scrap
        if doctrine in (DOCTRINE_FLUX, DOCTRINE_EVENT):
            row = conn.execute(
                """
                SELECT of_id FROM manufacturing_orders
                WHERE article_id = ? AND status IN ('launched', 'in_progress')
                ORDER BY of_id ASC LIMIT 1
                """,
                (article,),
            ).fetchone()
            if row is not None:
                open_nc(
                    conn,
                    of_id=row["of_id"],
                    qty_concerned=qty_scrap,
                    explanation=f"Hazard NC qualité scénario {scenario.name}",
                    severity=payload.get("severity", "normal"),
                )
                scrap_nc(
                    conn,
                    of_id=row["of_id"],
                    qty_scrapped=qty_scrap,
                    explanation="L4 hazard scrap",
                )
                state.quality_nc_applied = True
                result.hazards_observed.append(
                    {"day": hazard.day, "kind": kind, "of_id": row["of_id"],
                     "qty_scrap": qty_scrap}
                )
        else:
            # Doctrine OF : on enregistre l'effet (scrap) sans porter de NC
            state.quality_nc_applied = True
            result.hazards_observed.append(
                {"day": hazard.day, "kind": kind, "article_id": article,
                 "qty_scrap": qty_scrap, "note": "doctrine_of_no_quality_module"}
            )
    elif kind == HAZARD_PO_DELAY:
        po_id = payload["po_id"]
        delay = int(payload["delay_days"])
        # Décale expected_at du PO
        row = conn.execute(
            "SELECT expected_at FROM purchase_orders WHERE po_id = ?",
            (po_id,),
        ).fetchone()
        if row is not None and row["expected_at"]:
            old = datetime.fromisoformat(row["expected_at"])
            new = (old + timedelta(days=delay)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "UPDATE purchase_orders SET expected_at = ? WHERE po_id = ?",
                (new, po_id),
            )
            state.po_delays[po_id] = delay
            result.hazards_observed.append(
                {"day": hazard.day, "kind": kind, "po_id": po_id,
                 "delay_days": delay}
            )
    elif kind == HAZARD_URGENT_ORDER:
        so_id = payload["sales_order_id"]
        due_date = _day_to_iso(
            scenario.horizon_start, int(payload["due_day"])
        )[:10]
        conn.execute(
            """
            INSERT INTO sales_orders
                (sales_order_id, article_id, quantity, due_date)
            VALUES (?, ?, ?, ?)
            """,
            (so_id, payload["article_id"], float(payload["quantity"]), due_date),
        )
        state.urgent_order_id = so_id
        result.hazards_observed.append(
            {"day": hazard.day, "kind": kind, "sales_order_id": so_id,
             "quantity": payload["quantity"]}
        )
        # La gestion du replan est doctrine-specific (cf. boucle ci-dessous).


def _decay_breakdowns(state: HazardState) -> None:
    expired = [ws for ws, d in state.breakdown_ws.items() if d <= 0]
    for ws in expired:
        state.breakdown_ws.pop(ws, None)
        state.breakdown_factor.pop(ws, None)
    for ws in list(state.breakdown_ws):
        state.breakdown_ws[ws] -= 1


# ----------------------------------------------------------------------
# Boucle d'exécution MES (commune aux 3 doctrines)
# ----------------------------------------------------------------------


def _of_blocked_by_pending_component(
    conn: sqlite3.Connection, of_id: str
) -> bool:
    """Renvoie True si l'OF utilise (BOM) un composant fabriqué dont l'OF
    n'est pas encore clôturé.

    On regarde les bom_lines de l'article + le statut des OFs sur le child_article.
    """
    children = conn.execute(
        """
        SELECT b.child_article FROM bom_lines b
        JOIN articles a ON a.article_id = b.child_article
        JOIN manufacturing_orders mo ON mo.of_id = ?
        WHERE b.parent_article = mo.article_id AND a.is_purchased = 0
        """,
        (of_id,),
    ).fetchall()
    if not children:
        return False
    for c in children:
        pending = conn.execute(
            """
            SELECT COUNT(*) AS n FROM manufacturing_orders
            WHERE article_id = ?
              AND status IN ('created', 'launched', 'in_progress')
            """,
            (c["child_article"],),
        ).fetchone()
        if pending and int(pending["n"]) > 0:
            return True
    return False


def _advance_one_day(
    conn: sqlite3.Connection,
    scenario: Scenario,
    day: int,
    state: HazardState,
    result: RunResult,
    rng: random.Random,
    *,
    actor: str,
    apply_quality_scrap_to_op: bool = False,
) -> None:
    """Pour chaque OF en cours, exécute UNE opération.

    Respecte la chaîne BOM : un ART-A attend que son SEMI-1 soit clôturé.
    Si l'op tombe sur un poste en panne, end_day = day + 1 (1 jour de retard).
    """
    active = conn.execute(
        """
        SELECT of_id FROM manufacturing_orders
        WHERE status IN ('launched', 'in_progress')
        ORDER BY of_id ASC
        """
    ).fetchall()
    for row in active:
        of_id = row["of_id"]
        if _of_blocked_by_pending_component(conn, of_id):
            continue
        # Cherche la prochaine op pending
        op = conn.execute(
            """
            SELECT of_op_id, workstation_id, sequence_idx
            FROM order_operations
            WHERE of_id = ? AND status = 'pending'
            ORDER BY sequence_idx ASC LIMIT 1
            """,
            (of_id,),
        ).fetchone()
        if op is None:
            continue
        qty_row = conn.execute(
            "SELECT quantity FROM manufacturing_orders WHERE of_id = ?",
            (of_id,),
        ).fetchone()
        qty = float(qty_row["quantity"])
        # Effet panne : on déplace l'op au jour suivant si le poste est en panne
        ws = op["workstation_id"]
        if ws in state.breakdown_ws:
            # Op effectuée au jour day mais end_day = day + 1 pour signaler
            # un retard d'un jour (slowdown_factor sert seulement à indiquer
            # qu'il y a effectivement panne).
            end_day = day + 1
            # Apply scrap if a quality NC hazard hit this OF this turn
            base_scrap = max(0.0, round(qty * 0.05))
            scrap_extra = 0.0
            if apply_quality_scrap_to_op and not state.quality_nc_applied:
                scrap_extra = 15.0
                state.quality_nc_applied = True
            qty_scrap = min(qty, base_scrap + scrap_extra)
            qty_good = max(0.0, qty - qty_scrap)
            sid, fid = _execute_op(
                conn, op["of_op_id"],
                horizon_start=scenario.horizon_start, day=day,
                qty_good=qty_good, qty_scrap=qty_scrap, actor=actor,
            )
            _stamp_event_at_day(conn, fid, scenario.horizon_start, end_day, 480)
            _stamp_op_actual(
                conn, op["of_op_id"], scenario.horizon_start,
                start_day=day, end_day=end_day,
                minutes_offset_start=0, minutes_offset_end=480,
            )
        else:
            base_scrap = max(0.0, round(qty * 0.05))
            scrap_extra = 0.0
            if apply_quality_scrap_to_op and not state.quality_nc_applied:
                scrap_extra = 15.0
                state.quality_nc_applied = True
            qty_scrap = min(qty, base_scrap + scrap_extra)
            qty_good = max(0.0, qty - qty_scrap)
            _execute_op(
                conn, op["of_op_id"],
                horizon_start=scenario.horizon_start, day=day,
                qty_good=qty_good, qty_scrap=qty_scrap, actor=actor,
            )

        # Si c'était la dernière op, on clôture l'OF
        remaining = conn.execute(
            """
            SELECT COUNT(*) AS n FROM order_operations
            WHERE of_id = ? AND status != 'done'
            """,
            (of_id,),
        ).fetchone()
        if remaining and int(remaining["n"]) == 0:
            close_day = day if ws not in state.breakdown_ws else (day + 1)
            _close_of_at_day(
                conn, of_id, horizon_start=scenario.horizon_start,
                day=close_day, actor=actor,
            )
            result.of_closed_day[of_id] = close_day
            of_row = conn.execute(
                "SELECT quantity, qty_good, qty_scrap FROM manufacturing_orders "
                "WHERE of_id = ?",
                (of_id,),
            ).fetchone()
            if of_row:
                result.of_quantities[of_id] = float(of_row["quantity"])
                result.of_qty_good[of_id] = float(of_row["qty_good"])
                result.of_qty_scrap[of_id] = float(of_row["qty_scrap"])


def _measure_wip(
    conn: sqlite3.Connection, result: RunResult, day: int
) -> None:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM manufacturing_orders "
        "WHERE status IN ('launched', 'in_progress')"
    ).fetchone()
    result.daily_wip[day] = int(row["n"]) if row else 0


def _receive_due_purchase_orders(
    conn: sqlite3.Connection, scenario: Scenario, day: int
) -> None:
    target_iso = _day_to_iso(scenario.horizon_start, day)
    rows = conn.execute(
        """
        SELECT po_id, qty_ordered, qty_received, status
        FROM purchase_orders
        WHERE status IN ('open', 'partial')
          AND expected_at IS NOT NULL
          AND expected_at <= ?
        """,
        (target_iso,),
    ).fetchall()
    for row in rows:
        remaining = float(row["qty_ordered"]) - float(row["qty_received"])
        if remaining > 0:
            receive_purchase(conn, row["po_id"], qty_received=remaining)


# ----------------------------------------------------------------------
# Runner : doctrine OF (V0 - APS+MES OF-driven)
# ----------------------------------------------------------------------


def run_of_doctrine(
    scenario: Scenario, db_path: Path, *, fixtures_dir: Path = DEFAULT_FIXTURES_DIR
) -> RunResult:
    """Doctrine V0 : commandes → OF directs, pas de contrat de flux."""
    _bootstrap_db(scenario, db_path, fixtures_dir)
    result = RunResult(
        doctrine=DOCTRINE_OF, scenario_name=scenario.name,
        db_path=db_path, seed=scenario.seed,
    )
    rng = random.Random(scenario.seed)
    state = HazardState()

    with db_session(db_path) as conn:
        # Jour 0 : P1 immédiat sur les SO initiaux (pas de contrat de flux)
        p1 = run_p1_promotion(conn, actor="of.p1")
        result.aps_recalculations += 1
        for plan in p1.ofs_created:
            result.of_created_day[plan.of_id] = 0
            _launch_of_at_day(
                conn, plan.of_id, horizon_start=scenario.horizon_start,
                day=0, actor="of.mes",
            )

        hazards_by_day: dict[int, list] = {}
        for h in scenario.hazards:
            hazards_by_day.setdefault(h.day, []).append(h)

        apply_quality_next_day: bool = False
        for day in range(1, scenario.horizon_days + 1):
            _receive_due_purchase_orders(conn, scenario, day)
            # Aléas — la doctrine OF replanifie globalement sur CHAQUE aléa,
            # n'ayant ni dual tolerance ni détection événementielle.
            for h in hazards_by_day.get(day, []):
                _apply_hazard(conn, scenario, h, state, result, DOCTRINE_OF)
                if h.kind == HAZARD_QUALITY_NC:
                    apply_quality_next_day = True
                if h.kind == HAZARD_URGENT_ORDER:
                    new = run_p1_promotion(conn, actor="of.p1")
                    result.aps_recalculations += 1
                    for plan in new.ofs_created:
                        result.of_created_day[plan.of_id] = day
                        _launch_of_at_day(
                            conn, plan.of_id,
                            horizon_start=scenario.horizon_start,
                            day=day, actor="of.mes",
                        )
                else:
                    # Pour breakdown, quality_nc, po_delay : pas de détection
                    # événementielle, donc replan APS de précaution (réactif).
                    compute_candidates(conn)
                    result.aps_recalculations += 1
            _advance_one_day(
                conn, scenario, day, state, result, rng,
                actor="of.mes",
                apply_quality_scrap_to_op=apply_quality_next_day,
            )
            apply_quality_next_day = False
            _decay_breakdowns(state)
            _measure_wip(conn, result, day)
    return result


# ----------------------------------------------------------------------
# Helpers communs FLUX et EVENT (contrats + freeze)
# ----------------------------------------------------------------------


def _freeze_initial_contract(
    conn: sqlite3.Connection, scenario: Scenario, result: RunResult
) -> str:
    """Effectue CBN + P2 + contrat de flux + lissage + extinction + P3 freeze.

    Renvoie le batch_id de la tranche gelée.
    """
    compute_candidates(conn)
    result.aps_recalculations += 1
    run_p2_on_libre_zone(conn)
    cids = [
        r["candidate_id"]
        for r in conn.execute(
            "SELECT candidate_id FROM candidate_orders "
            "WHERE zone = 'negociable' ORDER BY candidate_id ASC"
        )
    ]
    if not cids:
        raise RuntimeError(
            "Aucun candidate en zone négociable : la doctrine flux ne peut "
            "pas former de contrat (vérifier P2)."
        )
    horizon_end = (
        datetime.fromisoformat(scenario.horizon_start)
        + timedelta(days=scenario.horizon_days)
    ).strftime("%Y-%m-%d")
    contract = create_contract(
        conn,
        horizon_label=f"W-{scenario.name}",
        horizon_start=scenario.horizon_start,
        horizon_end=horizon_end,
        candidate_ids=cids,
    )
    compute_coherence(conn, contract.contract_id)
    compute_smoothing(conn, contract.contract_id)
    for d in list_risk_debts(conn, status="open"):
        extinguish_risk_debt(conn, d.risk_debt_id, reason="L4 study seed")
    batch = run_p3_freeze(conn, contract.contract_id)
    return batch.batch_id


def _promote_frozen_candidates_to_ofs(
    conn: sqlite3.Connection, scenario: Scenario, batch_id: str, result: RunResult
) -> None:
    """Une fois gelés, les candidates sont promus en OFs lancés."""
    from pilotage_flux.aps.planner import promote_candidate_to_of

    cids = conn.execute(
        """
        SELECT DISTINCT l.candidate_id
        FROM freeze_batch_contracts fbc
        JOIN flux_contract_links l
          ON l.contract_id = fbc.contract_id AND l.version = fbc.version
        JOIN candidate_orders co ON co.candidate_id = l.candidate_id
        WHERE fbc.batch_id = ? AND co.status = 'candidate'
        ORDER BY l.candidate_id ASC
        """,
        (batch_id,),
    ).fetchall()
    for row in cids:
        plan = promote_candidate_to_of(conn, row["candidate_id"], actor="flux.p1")
        result.of_created_day[plan.of_id] = 0
        _launch_of_at_day(
            conn, plan.of_id, horizon_start=scenario.horizon_start,
            day=0, actor="flux.mes",
        )


# ----------------------------------------------------------------------
# Runner : doctrine FLUX (V1+V2)
# ----------------------------------------------------------------------


def run_flux_doctrine(
    scenario: Scenario, db_path: Path, *, fixtures_dir: Path = DEFAULT_FIXTURES_DIR
) -> RunResult:
    """Doctrine V1+V2 : contrats de flux + portes + freeze. Pas d'event sourcing."""
    _bootstrap_db(scenario, db_path, fixtures_dir)
    result = RunResult(
        doctrine=DOCTRINE_FLUX, scenario_name=scenario.name,
        db_path=db_path, seed=scenario.seed,
    )
    rng = random.Random(scenario.seed)
    state = HazardState()

    with db_session(db_path) as conn:
        batch_id = _freeze_initial_contract(conn, scenario, result)
        result.batch_id = batch_id
        _promote_frozen_candidates_to_ofs(conn, scenario, batch_id, result)

        hazards_by_day: dict[int, list] = {}
        for h in scenario.hazards:
            hazards_by_day.setdefault(h.day, []).append(h)

        apply_quality_next_day = False
        for day in range(1, scenario.horizon_days + 1):
            _receive_due_purchase_orders(conn, scenario, day)
            # FLUX : sans régulation événementielle, replan APS sur chaque aléa.
            for h in hazards_by_day.get(day, []):
                _apply_hazard(conn, scenario, h, state, result, DOCTRINE_FLUX)
                if h.kind == HAZARD_QUALITY_NC:
                    apply_quality_next_day = True
                if h.kind == HAZARD_URGENT_ORDER:
                    compute_candidates(conn)
                    result.aps_recalculations += 1
                    p1 = run_p1_promotion(conn, actor="flux.p1")
                    for plan in p1.ofs_created:
                        result.of_created_day[plan.of_id] = day
                        _launch_of_at_day(
                            conn, plan.of_id,
                            horizon_start=scenario.horizon_start,
                            day=day, actor="flux.mes",
                        )
                else:
                    compute_candidates(conn)
                    result.aps_recalculations += 1
            _advance_one_day(
                conn, scenario, day, state, result, rng,
                actor="flux.mes",
                apply_quality_scrap_to_op=apply_quality_next_day,
            )
            apply_quality_next_day = False
            _decay_breakdowns(state)
            _measure_wip(conn, result, day)
    return result


# ----------------------------------------------------------------------
# Runner : doctrine EVENT (V3)
# ----------------------------------------------------------------------


def run_event_doctrine(
    scenario: Scenario, db_path: Path, *, fixtures_dir: Path = DEFAULT_FIXTURES_DIR
) -> RunResult:
    """Doctrine V3 : V1+V2 + événements attendus, matching, dual tolerance, causes, mémoire."""
    _bootstrap_db(scenario, db_path, fixtures_dir)
    result = RunResult(
        doctrine=DOCTRINE_EVENT, scenario_name=scenario.name,
        db_path=db_path, seed=scenario.seed,
    )
    rng = random.Random(scenario.seed)
    state = HazardState()

    with db_session(db_path) as conn:
        # Configuration data-driven du filtre dual.
        # Tolérance temporelle calibrée pour la maille jour (1440 min = 1 jour) :
        # un écart d'1 jour -> score 1.0, 2 jours -> 1.0 saturé.
        # CPM absorbe les petits écarts (< 4h).
        # Seuils élevés -> on n'escalade qu'avec récurrence ou écart majeur.
        for name, value in [
            ("matching_time_tolerance_minutes", 1440.0),  # 1 jour
            ("cpm_margin_minutes", 240.0),                # 4h
            ("tolerance_threshold_watch", 0.20),
            ("tolerance_threshold_correct_local", 0.50),
            ("tolerance_threshold_replan_local", 1.00),
            ("tolerance_threshold_escalate", 2.00),
            ("tolerance_threshold_replan_global", 3.50),
        ]:
            conn.execute(
                "INSERT INTO parameters (scope, scope_ref, name, value_num) "
                "VALUES ('global', NULL, ?, ?)",
                (name, value),
            )
        batch_id = _freeze_initial_contract(conn, scenario, result)
        result.batch_id = batch_id
        _promote_frozen_candidates_to_ofs(conn, scenario, batch_id, result)
        generate_expected_from_batch(conn, batch_id)

        hazards_by_day: dict[int, list] = {}
        for h in scenario.hazards:
            hazards_by_day.setdefault(h.day, []).append(h)

        apply_quality_next_day = False
        for day in range(1, scenario.horizon_days + 1):
            _receive_due_purchase_orders(conn, scenario, day)
            for h in hazards_by_day.get(day, []):
                _apply_hazard(conn, scenario, h, state, result, DOCTRINE_EVENT)
                if h.kind == HAZARD_QUALITY_NC:
                    apply_quality_next_day = True
                # EVENT doctrine : pour HAZARD_URGENT_ORDER, on NE déclenche
                # PAS de replan global immédiat — la régulation événementielle
                # détectera et qualifiera l'écart, et c'est le filtre dual qui
                # décide du niveau d'action.
                if h.kind == HAZARD_URGENT_ORDER:
                    # On enregistre une re-CBN locale (besoin)
                    compute_candidates(conn)
                    result.aps_recalculations += 1
                    p1 = run_p1_promotion(conn, actor="event.p1")
                    for plan in p1.ofs_created:
                        result.of_created_day[plan.of_id] = day
                        _launch_of_at_day(
                            conn, plan.of_id,
                            horizon_start=scenario.horizon_start,
                            day=day, actor="event.mes",
                        )
            _advance_one_day(
                conn, scenario, day, state, result, rng,
                actor="event.mes",
                apply_quality_scrap_to_op=apply_quality_next_day,
            )
            apply_quality_next_day = False
            _decay_breakdowns(state)
            _measure_wip(conn, result, day)

            # Détection continue : match → causes → décision dual tolérance
            match_actuals_to_expected(conn, batch_id)
            for d in list_deviations(conn):
                if not d.is_absorbed:
                    attach_causes_to_deviation(conn, d.deviation_id)
            evaluate_all_open_deviations(conn, batch_id=batch_id)

        # Capture mémoire P4 sur les OF clôturés
        closed = conn.execute(
            "SELECT of_id FROM manufacturing_orders WHERE status = 'closed' "
            "ORDER BY of_id ASC"
        ).fetchall()
        for row in closed:
            capture_recipe(conn, of_id=row["of_id"], outcome="success")
    return result


# ----------------------------------------------------------------------
# Dispatch
# ----------------------------------------------------------------------


_DISPATCH = {
    DOCTRINE_OF: run_of_doctrine,
    DOCTRINE_FLUX: run_flux_doctrine,
    DOCTRINE_EVENT: run_event_doctrine,
}


def run_doctrine(
    scenario: Scenario,
    doctrine: str,
    db_path: Path,
    *,
    fixtures_dir: Path = DEFAULT_FIXTURES_DIR,
) -> RunResult:
    if doctrine not in DOCTRINES:
        raise ValueError(
            f"Doctrine inconnue : {doctrine!r} (attendu : {DOCTRINES})"
        )
    return _DISPATCH[doctrine](scenario, db_path, fixtures_dir=fixtures_dir)
