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

from pilotage_flux.aps import (
    arbitrate_routing_for_of,
    compute_candidates,
    routing_strategy_of,
)
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
    DOCTRINE_OF_EVENT,
    DOCTRINE_OF_MILP,
    DOCTRINES,
    HAZARD_BREAKDOWN,
    HAZARD_LOGISTIC_DELAY,
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
    corrective_actions_applied: list[dict[str, Any]] = field(default_factory=list)


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


# Point 3 paper — overrides paramétriques applicables post-bootstrap
# via global module-level state (set/reset par run_doctrine).
_PENDING_PARAM_OVERRIDES: dict | None = None


def _apply_pending_param_overrides(conn: sqlite3.Connection) -> None:
    """Applique les overrides paramétriques en attente (Point 3 paper).

    Override = bump de version, valid_to ancien posé. Idempotent
    par run.
    """
    global _PENDING_PARAM_OVERRIDES
    if not _PENDING_PARAM_OVERRIDES:
        return
    for (scope, scope_ref, name), value in _PENDING_PARAM_OVERRIDES.items():
        conn.execute(
            """
            UPDATE parameters SET valid_to = datetime('now')
            WHERE scope = ? AND (scope_ref IS ? OR scope_ref = ?)
              AND name = ? AND valid_to IS NULL
            """,
            (scope, scope_ref, scope_ref, name),
        )
        row = conn.execute(
            """
            SELECT COALESCE(MAX(version), 0) + 1 AS v FROM parameters
            WHERE scope = ? AND (scope_ref IS ? OR scope_ref = ?) AND name = ?
            """,
            (scope, scope_ref, scope_ref, name),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO parameters (scope, scope_ref, name, value_num, version)
            VALUES (?, ?, ?, ?, ?)
            """,
            (scope, scope_ref, name, float(value), int(row["v"])),
        )


def _bootstrap_db(
    scenario: Scenario, db_path: Path, fixtures_dir: Path
) -> None:
    """Crée la DB, importe les référentiels et applique l'état initial.

    L11.3 : si `<fixtures_dir>/routing_alternatives.csv` existe, l'utilise
    pour seeder la table routing_alternatives. Sinon, fixtures sans
    alternatives (l'arbitrage L11.2 sera linéaire pur).

    Point 3 paper : applique les overrides paramétriques pending en
    fin de bootstrap (après seed_defaults via import_referentials).
    """
    init_schema(db_path, drop_existing=True)
    with db_session(db_path) as conn:
        import_referentials(conn, fixtures_dir)
        _seed_routing_alternatives_from_csv(conn, fixtures_dir)
        _seed_bom_op_consumption_from_routing(conn)  # V13.1
        _seed_workstation_yields(conn)               # V13.B (item 4)
        _import_sales_orders(conn, scenario)
        _setup_stocks_and_pos(conn, scenario)
        _apply_pending_param_overrides(conn)


def _seed_routing_alternatives_from_csv(
    conn: sqlite3.Connection, fixtures_dir: Path,
) -> int:
    """Lit `routing_alternatives.csv` (si présent) et seed la table."""
    import csv as _csv

    csv_path = fixtures_dir / "routing_alternatives.csv"
    if not csv_path.exists():
        return 0
    n_added = 0
    with csv_path.open(encoding="utf-8") as f:
        for row in _csv.DictReader(f):
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO routing_alternatives "
                    "(article_id, sequence_idx, workstation_id, "
                    " unit_time_min, preference_order) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        row["article_id"],
                        int(row["sequence_idx"]),
                        row["workstation_id"],
                        float(row["unit_time_min"]),
                        int(row.get("preference_order") or 200),
                    ),
                )
                n_added += 1
            except (sqlite3.IntegrityError, KeyError, ValueError):
                continue
    return n_added


def _arbitrate_of_routing(
    conn: sqlite3.Connection,
    of_id: str,
    result: RunResult,
) -> None:
    """L11.4 : appelle l'arbitrage CPM-aware sur un OF nouvellement créé.

    Trace la stratégie résultante (linear/parallel/hybrid) dans
    `result.notes` la première fois qu'elle apparaît pour observabilité.
    """
    decisions = arbitrate_routing_for_of(conn, of_id)
    strategy = routing_strategy_of(decisions)
    if strategy != "linear":
        savings = sum(d.savings_min for d in decisions)
        result.notes.append(
            f"arbitrage OF {of_id}: {strategy} (économie {savings:.0f} min)"
        )


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
    logistic_ws: set[str] = field(default_factory=set)
    # WS bloqués par HAZARD_LOGISTIC_DELAY : delay_days = breakdown_ws[ws]
    # (jours restants du blocage), pas (factor-1)*2.
    pending_nc_scrap: float = 0.0
    # qty additionnelle de scrap à appliquer au prochain op (cumulée NC en cascade)
    nc_count: int = 0
    # nb total de NCs vus dans ce run (déclenche qc_intervention V3)
    urgent_order_id: str | None = None
    po_delays: dict[str, int] = field(default_factory=dict)
    delayed_of_until: dict[str, int] = field(default_factory=dict)
    # of_id -> jour minimum à partir duquel l'OF peut avancer sa prochaine op

    # L8.1 — état de la boucle physique V3 étendue
    qc_intervention_active: bool = False
    # Si V3 a déclenché une intervention qualité : qty_scrap pending × 0.5
    pos_alt_sourced: set[str] = field(default_factory=set)
    # PO id pour lesquels V3 a déjà sourcé en alternative
    urgent_seen_count: int = 0
    # Nb d'urgent_order vus dans ce run. V3 sauve les replans APS au-delà du 1er.

    # L9.4 — lissage : map of_id -> jour logique de lancement (selon smoothing)
    scheduled_launch_day: dict[str, int] = field(default_factory=dict)

    # V13.G — MES DBR-aware : goulot dynamique protégé par budget strict
    dbr_bottleneck_ws: str | None = None
    dbr_budget_min: float = 0.0  # minutes utilisables/jour sur le goulot


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
        # Cumul du scrap pending : la prochaine op processed l'absorbera.
        # Les NC en cascade s'additionnent jusqu'à consommation.
        state.pending_nc_scrap += qty_scrap
        state.nc_count += 1
        # Trouve un OF en cours sur cet article et trace NC + scrap (V2/V3 only)
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
                result.hazards_observed.append(
                    {"day": hazard.day, "kind": kind, "of_id": row["of_id"],
                     "qty_scrap": qty_scrap}
                )
            else:
                result.hazards_observed.append(
                    {"day": hazard.day, "kind": kind, "article_id": article,
                     "qty_scrap": qty_scrap}
                )
        else:
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
    elif kind == HAZARD_LOGISTIC_DELAY:
        # §24.9 — Flux logistique interne bloqué : le poste est inopérant
        # (pas de mise à disposition). Mécaniquement équivalent à un
        # breakdown sévère, sémantiquement distinct.
        ws = payload["workstation_id"]
        block_days = int(payload.get("block_days", 2))
        # Slowdown factor élevé = arrêt effectif. On garde 99.0 pour rester
        # détectable dans les KPIs sans diviser par zéro.
        state.breakdown_ws[ws] = block_days
        state.breakdown_factor[ws] = 99.0
        state.logistic_ws.add(ws)
        result.hazards_observed.append(
            {"day": hazard.day, "kind": kind, "workstation_id": ws,
             "block_days": block_days}
        )

    # ----- Hook BCE : propagation à travers la boucle cybernétique -----
    # Si la doctrine est BCE, le hazard est aussi propagé via emit_hazard
    # (C.2) → MACRS Couche 2 + filtre dual + delta_decision +
    # approval_queue. Pour les doctrines non-BCE, le hook ne fait rien.
    from pilotage_flux.comparative.bce_wire import bce_apply_hazard_hook
    bce_info = bce_apply_hazard_hook(
        conn, hazard, doctrine,
        day_iso=_day_to_iso(scenario.horizon_start, hazard.day),
    )
    if bce_info is not None and result.hazards_observed:
        # Enrichit la dernière trace hazard avec le résultat BCE
        result.hazards_observed[-1].update(bce_info)


def _get_mes_dbr_aware_flag(conn: sqlite3.Connection) -> bool:
    """V13.G — Lit `mes_dbr_aware` (default 0).

    1 = active la protection DBR du goulot au niveau MES :
        - identifie le goulot dynamique (argmax charge/capa)
        - impose un budget strict = daily_min × capa × target_sat sur
          ce WS (les ops en excès attendent le jour suivant)
        - les autres WS restent en mode legacy ou réaliste selon
          la config existante
    0 = comportement legacy (1 op/WS/jour) ou réaliste (V13.A).
    """
    from pilotage_flux.parameters import get_num
    val = get_num(
        conn, scope="global", scope_ref=None,
        name="mes_dbr_aware", default=0.0,
    )
    return bool(val and float(val) > 0.5)


def _get_mes_dbr_target_saturation(conn: sqlite3.Connection) -> float:
    """V13.G — Cible saturation pour le goulot MES (default 0.85)."""
    from pilotage_flux.parameters import get_num
    val = get_num(
        conn, scope="global", scope_ref=None,
        name="mes_dbr_target_saturation", default=0.85,
    )
    f = float(val) if val is not None else 0.85
    return min(0.99, max(0.30, f))


def _identify_mes_dbr_bottleneck(
    conn: sqlite3.Connection, target_saturation: float,
) -> tuple[str | None, float]:
    """V13.G — Goulot dynamique côté MES.

    Identification : argmax(charge_effective / capa) sur tous les
    candidates courants. La charge effective (qty × unit_time / capa)
    reflète le "temps machine" occupé — ce que l'exécution réaliste
    consomme sur le budget du WS.

    Budget DBR : `daily_capa_min × target_saturation`
    où daily_capa_min est le budget minutes/jour du realistic mode
    (V13.A). Le capa_factor est déjà encapsulé dans op_dur côté
    exécution — le budget doit être comparable à ces durées.

    Fallback si realistic mode inactif : daily_min du calendrier ×
    target_saturation (utilisable même en legacy pour informer).

    Renvoie (bottleneck_ws, budget_min_per_day).
    """
    from pilotage_flux.parameters import workstation_capacity_factor
    rows = conn.execute(
        """
        SELECT r.workstation_id AS ws,
               SUM(r.unit_time_min * c.quantity) AS load
        FROM candidate_orders c
        JOIN routing_operations r ON r.article_id = c.article_id
        WHERE c.status IN ('candidate', 'promoted')
        GROUP BY r.workstation_id
        """
    ).fetchall()
    if not rows:
        return None, 0.0
    daily_capa_realistic = _get_realistic_capacity_minutes_per_day(conn)
    if daily_capa_realistic > 0:
        daily_budget_base = daily_capa_realistic
    else:
        daily_min_row = conn.execute(
            "SELECT daily_minutes FROM calendars LIMIT 1"
        ).fetchone()
        daily_budget_base = int(daily_min_row["daily_minutes"]) if (
            daily_min_row and daily_min_row["daily_minutes"]
        ) else 480
    stats: dict[str, float] = {}
    for r in rows:
        ws = r["ws"]
        capa = workstation_capacity_factor(conn, ws)
        load_eff = float(r["load"] or 0) / max(capa, 0.01)
        stats[ws] = load_eff
    bottleneck = max(stats, key=lambda w: stats[w])
    budget = daily_budget_base * target_saturation
    return bottleneck, budget


def _get_realistic_capacity_minutes_per_day(
    conn: sqlite3.Connection,
) -> int:
    """V13.A — Lit `realistic_capacity_minutes_per_day` (default 0).

    0  → legacy : 1 op / WS / jour (sérialisation simplifiée)
    >0 → réaliste : un WS peut traiter N ops tant que la somme des
         durées (qty × unit_time / capacity_factor) ne dépasse pas
         ce budget journalier. Typique : 480 (1 shift 8h) ou 960
         (2 shifts).

    Cible le finding de l'audit §28.16 : la sérialisation 1-op-par-jour
    favorise structurellement les doctrines qui lancent le plus tôt.
    """
    from pilotage_flux.parameters import get_num
    val = get_num(
        conn, scope="global", scope_ref=None,
        name="realistic_capacity_minutes_per_day", default=0.0,
    )
    return int(val) if val is not None else 0


def _compute_op_duration_min(
    conn: sqlite3.Connection,
    of_id: str,
    sequence_idx: int,
    qty: float,
    workstation_id: str,
    state: "HazardState",
) -> float:
    """V13.A — Durée réelle d'une op en minutes.

    duration = qty × unit_time_min / capacity_factor(ws)
    Si le WS est en breakdown, on multiplie par le slowdown_factor.

    Cherche unit_time_min via `order_operations` (lié à `of_id` +
    `sequence_idx`) avec fallback sur `routing_operations`.
    """
    from pilotage_flux.parameters import workstation_capacity_factor
    row = conn.execute(
        """
        SELECT o.unit_time_min
        FROM order_operations o
        WHERE o.of_id = ? AND o.sequence_idx = ?
        """,
        (of_id, sequence_idx),
    ).fetchone()
    unit_time = float(row["unit_time_min"]) if row and row["unit_time_min"] else 0.0
    if unit_time <= 0:
        # Fallback : lit via routing
        of_art = conn.execute(
            "SELECT article_id FROM manufacturing_orders WHERE of_id = ?",
            (of_id,),
        ).fetchone()
        if of_art:
            r = conn.execute(
                "SELECT unit_time_min FROM routing_operations "
                "WHERE article_id = ? AND sequence_idx = ?",
                (of_art["article_id"], sequence_idx),
            ).fetchone()
            unit_time = float(r["unit_time_min"]) if r and r["unit_time_min"] else 1.0
        else:
            unit_time = 1.0
    capa = workstation_capacity_factor(conn, workstation_id)
    if capa <= 0:
        capa = 1.0
    base_dur = qty * unit_time / capa
    if workstation_id in state.breakdown_ws:
        factor = state.breakdown_factor.get(workstation_id, 1.5)
        base_dur *= factor
    return max(1.0, base_dur)


def _get_yield_compounding_flag(conn: sqlite3.Connection) -> bool:
    """V13.B (item 4) — Lit `yield_compounding_aware` (default 0).

    0 = legacy : scrap forfaitaire round(qty × 0.05) à chaque op,
        non-compounding, qty_good = 0.95 × qty (plafond OTIF mécanique).
    1 = modèle de rendement composé : chaque op applique le
        `yield_rate` de son workstation à la quantité bonne ENTRANTE
        (= sortie bonne de l'op précédente). Le qty_good final compose
        les pertes le long de la gamme → OTIF discrimine enfin par
        longueur de routing et qualité de poste. Brise le plafond 0.95.
    """
    from pilotage_flux.parameters import get_num
    val = get_num(
        conn, scope="global", scope_ref=None,
        name="yield_compounding_aware", default=0.0,
    )
    return bool(val and float(val) > 0.5)


def _seed_workstation_yields(conn: sqlite3.Connection) -> int:
    """V13.B — Seed un `yield_rate` par workstation s'il est absent.

    Étalement déterministe dans [0.96, 0.99] selon l'ordre des WS, pour
    que l'OTIF discrimine selon les postes traversés. N'écrase jamais
    une valeur existante (idempotent ; respecte les fixtures aléatoires
    qui seedent déjà leurs yields).
    """
    spread = [0.99, 0.985, 0.98, 0.975, 0.97, 0.965, 0.96]
    ws_rows = conn.execute(
        "SELECT workstation_id FROM workstations ORDER BY workstation_id"
    ).fetchall()
    n = 0
    for i, w in enumerate(ws_rows):
        wid = w["workstation_id"]
        existing = conn.execute(
            "SELECT 1 FROM parameters WHERE scope='workstation' "
            "AND scope_ref=? AND name='yield_rate' "
            "AND (valid_to IS NULL OR valid_to > datetime('now')) LIMIT 1",
            (wid,),
        ).fetchone()
        if existing is not None:
            continue
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('workstation', ?, 'yield_rate', ?)",
            (wid, spread[i % len(spread)]),
        )
        n += 1
    return n


def _compute_op_qty_good_scrap(
    conn: sqlite3.Connection,
    of_id: str,
    op: sqlite3.Row,
    of_qty: float,
    pending_scrap: float,
    *,
    compounding: bool,
) -> tuple[float, float, float]:
    """Calcule (qty_good, qty_scrap, pending_restant) pour une op.

    `compounding=False` (legacy) : scrap = round(qty × 0.05) + pending,
    sur la quantité de l'OF (non-compounding).

    `compounding=True` (V13.B) : la quantité bonne entrante est la
    sortie bonne de l'op précédente (seq_idx-1) ; on lui applique le
    `yield_rate` du poste. Le scrap composé + le pending sont retirés.
    """
    from pilotage_flux.parameters import workstation_yield_rate

    pending_remaining = pending_scrap
    if not compounding:
        base_scrap = max(0.0, round(of_qty * 0.05))
        extra = 0.0
        if pending_scrap > 0:
            extra = pending_scrap
            pending_remaining = 0.0
        qty_scrap = min(of_qty, base_scrap + extra)
        return max(0.0, of_qty - qty_scrap), qty_scrap, pending_remaining

    # Compounding : quantité bonne entrante = bonne de l'op précédente
    seq = int(op["sequence_idx"])
    prev = conn.execute(
        "SELECT qty_good FROM order_operations "
        "WHERE of_id = ? AND sequence_idx = ? AND status = 'done'",
        (of_id, seq - 1),
    ).fetchone()
    incoming = float(prev["qty_good"]) if prev and prev["qty_good"] else of_qty
    y = workstation_yield_rate(conn, op["workstation_id"])
    base_good = round(incoming * y)
    base_scrap = incoming - base_good
    extra = 0.0
    if pending_scrap > 0:
        extra = pending_scrap
        pending_remaining = 0.0
    qty_scrap = min(incoming, base_scrap + extra)
    return max(0.0, incoming - qty_scrap), qty_scrap, pending_remaining


def _get_event_driven_smoothing_advance_days(
    conn: sqlite3.Connection,
) -> int:
    """V13.0 — Lit `event_driven_smoothing_advance_days` (default 0).

    Quand un corrective action est appliqué, les OFs encore en zone
    négociable (statut 'created') et impactés par l'action voient
    leur `scheduled_launch_day` avancé de ce nombre de jours
    (borné par day_current + 1).

    0 = désactive V13.0 (rétrocompat).
    """
    from pilotage_flux.parameters import get_num
    val = get_num(
        conn, scope="global", scope_ref=None,
        name="event_driven_smoothing_advance_days", default=0.0,
    )
    return int(val) if val is not None else 0


def _pull_forward_pending_ofs_by_ws(
    conn: sqlite3.Connection,
    state: "HazardState",
    ws_id: str,
    day_current: int,
    days_advance: int,
) -> list[str]:
    """V13.0 — Avance les OFs encore 'created' qui routent par ws_id.

    Effet : la panne sur ws_id étant résolue, les OFs en attente
    peuvent démarrer plus tôt qu'initialement smoothé.
    """
    if days_advance <= 0:
        return []
    rows = conn.execute(
        """
        SELECT DISTINCT m.of_id
        FROM manufacturing_orders m
        JOIN routing_operations r ON r.article_id = m.article_id
        WHERE m.status = 'created' AND r.workstation_id = ?
        """,
        (ws_id,),
    ).fetchall()
    affected: list[str] = []
    for r in rows:
        of_id = r["of_id"]
        if of_id not in state.scheduled_launch_day:
            continue
        old_day = state.scheduled_launch_day[of_id]
        new_day = max(day_current + 1, old_day - days_advance)
        if new_day < old_day:
            state.scheduled_launch_day[of_id] = new_day
            affected.append(of_id)
    return affected


def _pull_forward_pending_ofs_by_parent_article(
    conn: sqlite3.Connection,
    state: "HazardState",
    child_article_id: str,
    day_current: int,
    days_advance: int,
) -> list[str]:
    """V13.0 — Avance les OFs 'created' dont l'article est PARENT BOM
    de child_article_id (utilise ce composant).

    Effet : le composant fourni alternativement étant désormais
    disponible, les OFs parents peuvent démarrer plus tôt.
    """
    if days_advance <= 0:
        return []
    rows = conn.execute(
        """
        SELECT DISTINCT m.of_id
        FROM manufacturing_orders m
        JOIN bom_lines b ON b.parent_article = m.article_id
        WHERE m.status = 'created' AND b.child_article = ?
        """,
        (child_article_id,),
    ).fetchall()
    affected: list[str] = []
    for r in rows:
        of_id = r["of_id"]
        if of_id not in state.scheduled_launch_day:
            continue
        old_day = state.scheduled_launch_day[of_id]
        new_day = max(day_current + 1, old_day - days_advance)
        if new_day < old_day:
            state.scheduled_launch_day[of_id] = new_day
            affected.append(of_id)
    return affected


def _pull_forward_all_pending_ofs(
    conn: sqlite3.Connection,
    state: "HazardState",
    day_current: int,
    days_advance: int,
) -> list[str]:
    """V13.0 — Avance TOUS les OFs encore 'created'.

    Effet : intervention qualité couvre l'ensemble du futur ; on
    récupère du débit en avançant uniformément les lancements.
    """
    if days_advance <= 0:
        return []
    affected: list[str] = []
    for of_id, old_day in list(state.scheduled_launch_day.items()):
        # Ne touche que les OFs encore en attente de lancement
        row = conn.execute(
            "SELECT status FROM manufacturing_orders WHERE of_id = ?",
            (of_id,),
        ).fetchone()
        if row is None or row["status"] != "created":
            continue
        new_day = max(day_current + 1, old_day - days_advance)
        if new_day < old_day:
            state.scheduled_launch_day[of_id] = new_day
            affected.append(of_id)
    return affected


def _apply_corrective_actions(
    conn: sqlite3.Connection,
    scenario: Scenario,
    state: HazardState,
    result: RunResult,
    day: int,
) -> None:
    """Doctrine EVENT : applique les actions du filtre dual à la réalité physique.

    Ferme la boucle planifier → exécuter → mesurer → **réguler** → apprendre.

    L5.2 + L8.1 : V3 réagit à 4 familles d'aléas via le filtre dual :
      - breakdown_ws    : V3 ordonne la maintenance immédiate (clear)
      - quality_nc      : V3 déclenche une intervention qualité qui divise
                          par 2 le scrap futur jusqu'à la fin du scénario
      - po_delay        : V3 source en alternatif (réception immédiate du PO)
      - urgent_order    : voir _apply_hazard : V3 saute le replan APS au-delà
                          du 1er urgent (fragmentation locale au lieu de replan
                          global)

    Idempotence : run_metadata_applied_actions trace les décisions consommées
    pour éviter les déclenchements multiples.
    """
    _ensure_applied_actions_table(conn)
    rows = conn.execute(
        """
        SELECT td.decision_id, td.action_level, td.candidate_id
        FROM tolerance_filter_decisions td
        WHERE td.triggered_at IS NOT NULL
          AND td.action_level IN (
              'correct_local', 'replan_local', 'escalate', 'replan_global'
          )
          AND td.decision_id NOT IN (
            SELECT decision_id FROM run_metadata_applied_actions
          )
        ORDER BY td.decision_id ASC
        """,
    ).fetchall()

    # Marque toutes ces décisions comme consommées d'abord
    for r in rows:
        conn.execute(
            "INSERT INTO run_metadata_applied_actions (decision_id) VALUES (?)",
            (int(r["decision_id"]),),
        )

    if not rows:
        return

    decision_ref = int(rows[0]["decision_id"])
    action_ref = rows[0]["action_level"]

    # V13.0 — event-driven smoothing reactivity (gated par paramètre)
    advance_days = _get_event_driven_smoothing_advance_days(conn)

    # L5.2 — breakdown : clear immédiat des pannes
    if state.breakdown_ws:
        for ws in list(state.breakdown_ws.keys()):
            state.breakdown_ws.pop(ws, None)
            state.breakdown_factor.pop(ws, None)
            state.logistic_ws.discard(ws)
            result.corrective_actions_applied.append({
                "day": day,
                "decision_id": decision_ref,
                "action_level": action_ref,
                "workstation_id": ws,
                "effect": "breakdown_cleared",
            })
            # V13.0 : panne résolue → avance les OFs en attente sur ce WS
            if advance_days > 0:
                pulled = _pull_forward_pending_ofs_by_ws(
                    conn, state, ws, day, advance_days,
                )
                if pulled:
                    result.corrective_actions_applied.append({
                        "day": day,
                        "decision_id": decision_ref,
                        "action_level": action_ref,
                        "effect": "v13_0_smoothing_pulled_forward",
                        "ws_id": ws,
                        "of_ids": pulled,
                        "advance_days": advance_days,
                    })

    # L8.1.a — quality_nc : intervention qualité (réduit scrap futur de 50%)
    if state.nc_count >= 1 and not state.qc_intervention_active:
        state.qc_intervention_active = True
        result.corrective_actions_applied.append({
            "day": day,
            "decision_id": decision_ref,
            "action_level": action_ref,
            "effect": "quality_intervention_started",
        })
        # V13.0 : intervention qualité globale → avance tous les OFs futurs
        if advance_days > 0:
            pulled = _pull_forward_all_pending_ofs(
                conn, state, day, advance_days,
            )
            if pulled:
                result.corrective_actions_applied.append({
                    "day": day,
                    "decision_id": decision_ref,
                    "action_level": action_ref,
                    "effect": "v13_0_smoothing_pulled_forward",
                    "scope": "all_pending_qc",
                    "of_ids": pulled,
                    "advance_days": advance_days,
                })

    # L8.1.b — po_delay : sourcing alternatif (réception immédiate à l'horizon initial)
    for po_id in list(state.po_delays.keys()):
        if po_id in state.pos_alt_sourced:
            continue
        po_row = conn.execute(
            """
            SELECT po_id, article_id, qty_ordered, qty_received, expected_at, status
            FROM purchase_orders WHERE po_id = ?
            """,
            (po_id,),
        ).fetchone()
        if po_row is None or po_row["status"] in ("received", "cancelled"):
            continue
        remaining = float(po_row["qty_ordered"]) - float(po_row["qty_received"])
        if remaining <= 0:
            continue
        receive_purchase(conn, po_id, qty_received=remaining)
        state.pos_alt_sourced.add(po_id)
        result.corrective_actions_applied.append({
            "day": day,
            "decision_id": decision_ref,
            "action_level": action_ref,
            "po_id": po_id,
            "qty_alt_sourced": remaining,
            "effect": "po_alternative_sourced",
        })
        # V13.0 : composant désormais dispo → avance les OFs parents
        if advance_days > 0 and po_row["article_id"]:
            pulled = _pull_forward_pending_ofs_by_parent_article(
                conn, state, po_row["article_id"], day, advance_days,
            )
            if pulled:
                result.corrective_actions_applied.append({
                    "day": day,
                    "decision_id": decision_ref,
                    "action_level": action_ref,
                    "effect": "v13_0_smoothing_pulled_forward",
                    "po_id": po_id,
                    "child_article": po_row["article_id"],
                    "of_ids": pulled,
                    "advance_days": advance_days,
                })


def _ensure_applied_actions_table(conn: sqlite3.Connection) -> None:
    """Table légère pour suivre les décisions filtre dual déjà appliquées
    (idempotence : une décision ne déclenche un effet physique qu'une fois)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_metadata_applied_actions (
            decision_id INTEGER PRIMARY KEY
        )
        """
    )


def _decay_breakdowns(state: HazardState) -> None:
    expired = [ws for ws, d in state.breakdown_ws.items() if d <= 0]
    for ws in expired:
        state.breakdown_ws.pop(ws, None)
        state.breakdown_factor.pop(ws, None)
        state.logistic_ws.discard(ws)
    for ws in list(state.breakdown_ws):
        state.breakdown_ws[ws] -= 1


# ----------------------------------------------------------------------
# Boucle d'exécution MES (commune aux 3 doctrines)
# ----------------------------------------------------------------------


def _get_bom_op_linkage_flag(conn: sqlite3.Connection) -> bool:
    """V13.1 — Lit `bom_op_linkage_aware` (default 0).

    Active la liaison composant ↔ opération de gamme :
      - Op 1 d'un parent peut démarrer dès que ses composants
        spécifiques (consuming_operation_idx <= 1) sont prêts
      - Op N peut démarrer dès que les composants consumés à op
        ≤ N sont prêts

    Permet la production phasée — ce qu'un planificateur lean fait
    naturellement (« j'ai commencé op 1 dès SEMI-1 livré, sans
    attendre SEMI-2 »).
    """
    from pilotage_flux.parameters import get_num
    val = get_num(
        conn, scope="global", scope_ref=None,
        name="bom_op_linkage_aware", default=0.0,
    )
    return bool(val and float(val) > 0.5)


def _seed_bom_op_consumption_from_routing(conn: sqlite3.Connection) -> int:
    """V13.1 — Dispatch les bom_lines sur les ops du routing du parent.

    Heuristique simple : la i-ème ligne BOM d'un article est attribuée
    à l'op `min(i, n_ops)`. Premier composant à op 1, deuxième à op 2,
    etc. Si plus de composants que d'ops, les derniers vont sur la
    dernière op.

    Ne touche QUE les `bom_lines` avec `consuming_operation_idx IS NULL`
    (idempotent).
    """
    rows = conn.execute(
        """
        SELECT bom_line_id, parent_article, child_article
        FROM bom_lines
        WHERE consuming_operation_idx IS NULL
        ORDER BY parent_article, bom_line_id
        """,
    ).fetchall()
    if not rows:
        return 0
    n_assigned = 0
    by_parent: dict[str, list] = {}
    for r in rows:
        by_parent.setdefault(r["parent_article"], []).append(r)
    for parent, lines in by_parent.items():
        ops = conn.execute(
            "SELECT COUNT(*) AS n FROM routing_operations WHERE article_id = ?",
            (parent,),
        ).fetchone()
        n_ops = int(ops["n"] or 0) if ops else 0
        if n_ops < 1:
            n_ops = 1
        for i, line in enumerate(lines, start=1):
            op_idx = min(i, n_ops)
            conn.execute(
                "UPDATE bom_lines SET consuming_operation_idx = ? "
                "WHERE bom_line_id = ?",
                (op_idx, line["bom_line_id"]),
            )
            n_assigned += 1
    return n_assigned


def _components_needed_at_or_before_op(
    conn: sqlite3.Connection,
    parent_article: str,
    op_seq_idx: int,
) -> list[str]:
    """V13.1 — Liste des child_article consommés à l'op ≤ op_seq_idx.

    Si `consuming_operation_idx IS NULL`, on considère le composant
    requis à l'op 1 (legacy : tous les composants au démarrage).
    """
    rows = conn.execute(
        """
        SELECT child_article
        FROM bom_lines
        WHERE parent_article = ?
          AND COALESCE(consuming_operation_idx, 1) <= ?
        """,
        (parent_article, op_seq_idx),
    ).fetchall()
    return [r["child_article"] for r in rows]


def _of_op_blocked_by_pending_component(
    conn: sqlite3.Connection,
    of_id: str,
    op_seq_idx: int,
    *,
    op_aware: bool,
) -> bool:
    """V13.1 — Variante op-aware de `_of_blocked_by_pending_component`.

    Si `op_aware=False` : comportement legacy (block si TOUT composant
    en amont est encore en flight).

    Si `op_aware=True` : ne block que pour les composants dont
    `consuming_operation_idx <= op_seq_idx`. L'op 1 peut démarrer dès
    que ses composants spécifiques sont prêts, indépendamment des
    composants des ops 2+.
    """
    row = conn.execute(
        "SELECT candidate_id, article_id FROM manufacturing_orders "
        "WHERE of_id = ?",
        (of_id,),
    ).fetchone()
    if not row or row["candidate_id"] is None:
        return False
    cand_id = row["candidate_id"]
    parent_article = row["article_id"]
    # Composants requis à cette op (ou plus tôt) si op_aware ; sinon tous
    if op_aware and parent_article:
        needed = set(
            _components_needed_at_or_before_op(
                conn, parent_article, op_seq_idx,
            )
        )
    else:
        needed = None  # = legacy : tous composants en amont
    # Upstream candidates via pegging
    upstream = conn.execute(
        """
        SELECT pl.target_id, c.article_id AS up_article
        FROM pegging_links pl
        LEFT JOIN candidate_orders c ON c.candidate_id = pl.target_id
        WHERE pl.source_type = 'candidate_order'
          AND pl.source_id = ?
          AND pl.target_type = 'candidate_order'
        """,
        (cand_id,),
    ).fetchall()
    if not upstream:
        return False
    for up in upstream:
        # Filtre op-aware : seuls les composants requis à cette op
        if needed is not None and up["up_article"] not in needed:
            continue
        pending = conn.execute(
            """
            SELECT COUNT(*) AS n FROM manufacturing_orders
            WHERE candidate_id = ?
              AND status IN ('created', 'launched', 'in_progress')
            """,
            (up["target_id"],),
        ).fetchone()
        if pending and int(pending["n"]) > 0:
            return True
    return False


def _of_blocked_by_pending_component(
    conn: sqlite3.Connection, of_id: str
) -> bool:
    """Renvoie True si l'OF utilise (via pegging) un composant fabriqué
    dont l'OF dédié n'est pas encore clôturé.

    Wrapper legacy : appelle `_of_op_blocked_by_pending_component` en
    mode non-op-aware (bloque sur tout composant en flight).
    """
    return _of_op_blocked_by_pending_component(
        conn, of_id, op_seq_idx=1, op_aware=False,
    )


def _advance_one_day(
    conn: sqlite3.Connection,
    scenario: Scenario,
    day: int,
    state: HazardState,
    result: RunResult,
    rng: random.Random,
    *,
    actor: str,
) -> None:
    """Pour chaque OF en cours, exécute UNE opération sous deux contraintes :

    1. Chaîne BOM : un ART-A attend que son SEMI-1 soit clôturé.
    2. Sérialisation poste : un poste ne traite qu'UN OF par jour (capacité
       finie). Si plusieurs OF veulent le même poste, les autres attendent.

    Scrap pending (NC en cascade) : consommé par le 1er OF advancé du jour.
    V3 qc_intervention_active divise le scrap pending par 2.
    """
    active = conn.execute(
        """
        SELECT of_id FROM manufacturing_orders
        WHERE status IN ('launched', 'in_progress')
        ORDER BY of_id ASC
        """
    ).fetchall()
    # V13.A — bascule legacy (1 op/WS/jour) ⟷ réaliste (budget min/WS/jour)
    daily_capa_min = _get_realistic_capacity_minutes_per_day(conn)
    realistic = daily_capa_min > 0
    # V13.1 — BOM-op linkage : déblocage phase par phase
    bom_op_aware = _get_bom_op_linkage_flag(conn)
    # V13.B (item 4) — rendement composé par poste
    compounding = _get_yield_compounding_flag(conn)
    # V13.G — MES DBR-aware : identification lazy du goulot (une fois)
    dbr_aware = _get_mes_dbr_aware_flag(conn)
    if dbr_aware and state.dbr_bottleneck_ws is None:
        state.dbr_bottleneck_ws, state.dbr_budget_min = (
            _identify_mes_dbr_bottleneck(
                conn, _get_mes_dbr_target_saturation(conn),
            )
        )
    dbr_ws = state.dbr_bottleneck_ws if dbr_aware else None
    dbr_used_today = 0.0
    busy_ws: set[str] = set()  # legacy
    ws_minutes_used: dict[str, float] = {}  # realistic
    # Scrap cumulé des NC du jour (et précédents) à appliquer au 1er OF avancé.
    # V3 qc_intervention_active divise par 2 (l'intervention qualité limite
    # le dommage). Cumulatif : un cascade_nc voit chaque NC consommé tour à tour.
    pending_scrap = state.pending_nc_scrap
    if state.qc_intervention_active and pending_scrap > 0:
        pending_scrap = pending_scrap * 0.5
    state.pending_nc_scrap = 0.0  # consumé
    for row in active:
        of_id = row["of_id"]
        # OF retardé par un breakdown précédent : doit attendre 1 jour
        if state.delayed_of_until.get(of_id, 0) > day:
            continue
        # Cherche la prochaine op pending (besoin de sequence_idx avant
        # de tester le blocage BOM en mode op-aware)
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
        # V13.1 : blocage BOM phasé par op (legacy si flag off)
        if _of_op_blocked_by_pending_component(
            conn, of_id, int(op["sequence_idx"]), op_aware=bom_op_aware,
        ):
            continue
        qty_row = conn.execute(
            "SELECT quantity FROM manufacturing_orders WHERE of_id = ?",
            (of_id,),
        ).fetchone()
        qty = float(qty_row["quantity"])
        ws = op["workstation_id"]
        if realistic:
            # V13.A — réaliste : N ops/WS/jour tant que cap minutes pas dépassé
            op_dur = _compute_op_duration_min(
                conn, of_id, int(op["sequence_idx"]), qty, ws, state,
            )
            used = ws_minutes_used.get(ws, 0.0)
            if used + op_dur > daily_capa_min:
                continue
            # V13.G — Trace occupation goulot (utile pour Rope V14).
            # Note doctrinale : le goulot doit tourner à 100 % (Goldratt) ;
            # aucune contrainte de budget appliquée ici. Le vrai Rope
            # (cadence WS amont sur file goulot) sera implémenté en V14.
            if dbr_ws is not None and ws == dbr_ws:
                dbr_used_today += op_dur
            start_min = int(used)
            end_min = int(min(daily_capa_min, used + op_dur))
            ws_minutes_used[ws] = used + op_dur
            qty_good, qty_scrap, pending_scrap = _compute_op_qty_good_scrap(
                conn, of_id, op, qty, pending_scrap, compounding=compounding,
            )
            sid, fid = _execute_op(
                conn, op["of_op_id"],
                horizon_start=scenario.horizon_start, day=day,
                qty_good=qty_good, qty_scrap=qty_scrap, actor=actor,
            )
            # Réécrit les timestamps avec les minutes réelles
            _stamp_event_at_day(
                conn, sid, scenario.horizon_start, day, start_min,
            )
            _stamp_event_at_day(
                conn, fid, scenario.horizon_start, day, end_min,
            )
            _stamp_op_actual(
                conn, op["of_op_id"], scenario.horizon_start,
                start_day=day, end_day=day,
                minutes_offset_start=start_min,
                minutes_offset_end=end_min,
            )
            if ws in state.breakdown_ws:
                # Le breakdown ralentit déjà via op_dur ; pas de delayed_of_until
                # supplémentaire en mode réaliste
                pass
        elif ws in busy_ws:
            # Legacy : un poste ne peut traiter qu'un OF par jour
            # (le budget DBR V13.G n'est actif qu'en mode realistic V13.A)
            continue
        elif ws in state.breakdown_ws:
            busy_ws.add(ws)
            # Effet panne : retard proportionnel au slowdown_factor.
            # factor=1.5 -> +1 jour ; factor=2.0 -> +2 jours ; factor=3.0 -> +3 jours.
            factor = state.breakdown_factor.get(ws, 1.5)
            if ws in state.logistic_ws:
                # LOGISTIC_DELAY : delay = jours restants du blocage
                # (block_days est stocké dans breakdown_ws pour le décompte).
                delay_days = max(1, state.breakdown_ws[ws])
            else:
                delay_days = max(1, int(round((factor - 1.0) * 2)))
            end_day = day + delay_days
            qty_good, qty_scrap, pending_scrap = _compute_op_qty_good_scrap(
                conn, of_id, op, qty, pending_scrap, compounding=compounding,
            )
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
            # L5.2 : un OF qui a subi un breakdown doit attendre `delay_days`
            # avant que sa prochaine op puisse être avancée
            state.delayed_of_until[of_id] = end_day
        else:
            busy_ws.add(ws)
            qty_good, qty_scrap, pending_scrap = _compute_op_qty_good_scrap(
                conn, of_id, op, qty, pending_scrap, compounding=compounding,
            )
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
            close_day = day
            if ws in state.breakdown_ws:
                factor = state.breakdown_factor.get(ws, 1.5)
                if ws in state.logistic_ws:
                    close_day = day + max(1, state.breakdown_ws[ws])
                else:
                    close_day = day + max(1, int(round((factor - 1.0) * 2)))
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
            # L11.4 : arbitrage CPM-aware avant lancement
            _arbitrate_of_routing(conn, plan.of_id, result)
            result.of_created_day[plan.of_id] = 0
            _launch_of_at_day(
                conn, plan.of_id, horizon_start=scenario.horizon_start,
                day=0, actor="of.mes",
            )

        hazards_by_day: dict[int, list] = {}
        for h in scenario.hazards:
            hazards_by_day.setdefault(h.day, []).append(h)

        for day in range(1, scenario.horizon_days + 1):
            _receive_due_purchase_orders(conn, scenario, day)
            # Aléas — la doctrine OF replanifie globalement sur CHAQUE aléa,
            # n'ayant ni dual tolerance ni détection événementielle.
            for h in hazards_by_day.get(day, []):
                _apply_hazard(conn, scenario, h, state, result, DOCTRINE_OF)
                if h.kind == HAZARD_URGENT_ORDER:
                    new = run_p1_promotion(conn, actor="of.p1")
                    result.aps_recalculations += 1
                    for plan in new.ofs_created:
                        _arbitrate_of_routing(conn, plan.of_id, result)
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
                conn, scenario, day, state, result, rng, actor="of.mes",
            )
            _decay_breakdowns(state)
            _measure_wip(conn, result, day)
    return result


# ----------------------------------------------------------------------
# Helpers communs FLUX et EVENT (contrats + freeze)
# ----------------------------------------------------------------------


def _freeze_initial_contract(
    conn: sqlite3.Connection, scenario: Scenario, result: RunResult
) -> str:
    """Effectue CBN + P2 + contrat(s) de flux + lissage + extinction + P3 freeze.

    Comportement adaptatif :
      - Si scénario contient un seul groupe d'articles finis → 1 contrat,
        run_p3_freeze (chemin historique).
      - Si scénario contient >=2 articles finis distincts → 1 contrat par
        article fini, run_p3_collective_freeze (exerce L6.1).

    Renvoie le batch_id de la tranche gelée (commune si multi-contrats).
    """
    from pilotage_flux.gates import run_p3_collective_freeze

    compute_candidates(conn)
    result.aps_recalculations += 1
    run_p2_on_libre_zone(conn)
    rows = conn.execute(
        """
        SELECT candidate_id, article_id FROM candidate_orders
        WHERE zone = 'negociable'
        ORDER BY candidate_id ASC
        """
    ).fetchall()
    if not rows:
        raise RuntimeError(
            "Aucun candidate en zone négociable : la doctrine flux ne peut "
            "pas former de contrat (vérifier P2)."
        )

    horizon_end = (
        datetime.fromisoformat(scenario.horizon_start)
        + timedelta(days=scenario.horizon_days)
    ).strftime("%Y-%m-%d")

    # Groupe les candidates par article fini (= article qui n'est composant
    # d'aucun autre BOM dans le scope considéré). Pour simplifier : un
    # candidate est "fini" s'il n'apparaît pas comme child_article dans bom_lines.
    finished_articles_query = conn.execute(
        """
        SELECT DISTINCT a.article_id FROM articles a
        WHERE NOT EXISTS (
            SELECT 1 FROM bom_lines b WHERE b.child_article = a.article_id
        )
        """
    ).fetchall()
    finished_set = {r["article_id"] for r in finished_articles_query}

    # Map: finished_article -> [candidate_ids of that finished family]
    # Pour les SEMI-* : on les rattache au contrat du premier fini qui les utilise
    # (heuristique simple). Pour le V9, c'est suffisant ; un découpage plus fin
    # exigerait du pegging multi-niveau côté contrats — hors scope.
    family_by_finished: dict[str, list[str]] = {}
    finished_cands_by_article: dict[str, list[str]] = {}
    for r in rows:
        cand_id, article_id = r["candidate_id"], r["article_id"]
        if article_id in finished_set:
            finished_cands_by_article.setdefault(article_id, []).append(cand_id)
    # Découvre les SEMI utilisés par chaque fini via pegging
    semi_to_finished: dict[str, str] = {}
    for finished, cands in finished_cands_by_article.items():
        for cand in cands:
            peggings = conn.execute(
                """
                SELECT target_id FROM pegging_links
                WHERE source_type = 'candidate_order' AND source_id = ?
                  AND target_type = 'candidate_order'
                """,
                (cand,),
            ).fetchall()
            for p in peggings:
                semi_to_finished.setdefault(p["target_id"], finished)
    # Construit family_by_finished
    for r in rows:
        cand_id, article_id = r["candidate_id"], r["article_id"]
        if article_id in finished_set:
            family_by_finished.setdefault(article_id, []).append(cand_id)
        else:
            # Semi-fini : rattache au fini qui le pegging
            owner = semi_to_finished.get(cand_id)
            if owner:
                family_by_finished.setdefault(owner, []).append(cand_id)
            else:
                # Pas de fini parent → premier fini disponible
                first_finished = next(iter(family_by_finished), None)
                if first_finished is None and finished_cands_by_article:
                    first_finished = next(iter(finished_cands_by_article))
                if first_finished:
                    family_by_finished.setdefault(first_finished, []).append(cand_id)

    # Si une seule famille → chemin mono-contrat historique
    if len(family_by_finished) <= 1:
        all_cids = [r["candidate_id"] for r in rows]
        contract = create_contract(
            conn,
            horizon_label=f"W-{scenario.name}",
            horizon_start=scenario.horizon_start,
            horizon_end=horizon_end,
            candidate_ids=all_cids,
        )
        compute_coherence(conn, contract.contract_id)
        compute_smoothing(conn, contract.contract_id)
        for d in list_risk_debts(conn, status="open"):
            extinguish_risk_debt(conn, d.risk_debt_id, reason="L4 study seed")
        batch = run_p3_freeze(conn, contract.contract_id)
        return batch.batch_id

    # Multi-contrats : un contrat par famille → P3 collective
    contract_ids: list[str] = []
    for finished_article, cids in sorted(family_by_finished.items()):
        contract = create_contract(
            conn,
            horizon_label=f"W-{scenario.name}-{finished_article}",
            horizon_start=scenario.horizon_start,
            horizon_end=horizon_end,
            candidate_ids=cids,
        )
        compute_coherence(conn, contract.contract_id)
        compute_smoothing(conn, contract.contract_id)
        contract_ids.append(contract.contract_id)

    for d in list_risk_debts(conn, status="open"):
        extinguish_risk_debt(conn, d.risk_debt_id, reason="L9 multi-contracts")

    collective = run_p3_collective_freeze(
        conn, contract_ids, actor="flux.p3.collective"
    )
    # Trace la décision dans le RunResult avec multi-goulot (L10.3)
    multi = collective.bottleneck_workstations
    multi_str = ",".join(f"{ws}={ratio:.0%}" for ws, _, _, ratio in multi[:3])
    result.notes.append(
        f"P3 collective: {collective.decision}, "
        f"frozen={len(collective.frozen_contracts)}, "
        f"deferred={len(collective.deferred_contracts)}, "
        f"bottlenecks=[{multi_str}]"
    )
    if collective.batch_id is None:
        raise RuntimeError(
            f"P3 collective n'a gelé aucun contrat ({collective.decision}) — "
            "vérifier la capacité goulot vs charge"
        )
    return collective.batch_id


def _promote_frozen_candidates_to_ofs(
    conn: sqlite3.Connection, scenario: Scenario, batch_id: str,
    result: RunResult,
    *,
    state: HazardState | None = None,
    use_smoothing: bool = False,
    actor_prefix: str = "flux",
) -> dict[str, int]:
    """Promeut les candidates gelés en OFs.

    Si `use_smoothing=True`, lit `flux_smoothed_launches.planned_start` pour
    déterminer le jour de lancement de chaque OF. Les OFs au jour 0 sont
    lancés immédiatement ; les autres restent en statut 'created' et seront
    lancés par `_launch_scheduled_ofs` au jour adéquat.

    Retourne un mapping {of_id: scheduled_launch_day}.
    """
    from datetime import datetime
    from pilotage_flux.aps.planner import promote_candidate_to_of

    cids_rows = conn.execute(
        """
        SELECT DISTINCT l.candidate_id, l.contract_id, l.version
        FROM freeze_batch_contracts fbc
        JOIN flux_contract_links l
          ON l.contract_id = fbc.contract_id AND l.version = fbc.version
        JOIN candidate_orders co ON co.candidate_id = l.candidate_id
        WHERE fbc.batch_id = ? AND co.status = 'candidate'
        ORDER BY l.candidate_id ASC
        """,
        (batch_id,),
    ).fetchall()

    # Pré-calcule scheduled_day par candidate via flux_smoothed_launches
    scheduled_day_by_cand: dict[str, int] = {}
    if use_smoothing:
        base = datetime.fromisoformat(scenario.horizon_start)
        if base.hour == 0 and base.minute == 0:
            base = base.replace(hour=0)  # garde l'heure 0 pour le calcul jour
        smoothed_rows = conn.execute(
            """
            SELECT candidate_id, planned_start FROM flux_smoothed_launches
            ORDER BY candidate_id ASC
            """
        ).fetchall()
        for srow in smoothed_rows:
            try:
                planned_dt = datetime.fromisoformat(srow["planned_start"])
                delta_days = max(0, (planned_dt - base).days)
                scheduled_day_by_cand[srow["candidate_id"]] = delta_days
            except (ValueError, TypeError):
                scheduled_day_by_cand[srow["candidate_id"]] = 0

    of_to_day: dict[str, int] = {}
    for row in cids_rows:
        cand_id = row["candidate_id"]
        plan = promote_candidate_to_of(conn, cand_id, actor=f"{actor_prefix}.p1")
        # L11.4 : arbitrage routing CPM-aware juste après création
        _arbitrate_of_routing(conn, plan.of_id, result)
        scheduled = scheduled_day_by_cand.get(cand_id, 0)
        of_to_day[plan.of_id] = scheduled
        result.of_created_day[plan.of_id] = scheduled
        if scheduled == 0:
            _launch_of_at_day(
                conn, plan.of_id, horizon_start=scenario.horizon_start,
                day=0, actor=f"{actor_prefix}.mes",
            )

    if state is not None:
        state.scheduled_launch_day = of_to_day
    return of_to_day


def _launch_scheduled_ofs(
    conn: sqlite3.Connection,
    scenario: Scenario,
    state: HazardState,
    result: RunResult,
    day: int,
    *,
    actor: str,
) -> None:
    """Lance les OFs dont le scheduled_launch_day correspond au jour courant.

    Utilisé par les doctrines flux (avec smoothing). Pour OF/OF+EVENT, le
    state.scheduled_launch_day reste vide → cette fonction ne fait rien.
    """
    if not state.scheduled_launch_day:
        return
    for of_id, scheduled in list(state.scheduled_launch_day.items()):
        if scheduled != day:
            continue
        row = conn.execute(
            "SELECT status FROM manufacturing_orders WHERE of_id = ?",
            (of_id,),
        ).fetchone()
        if row is None or row["status"] != "created":
            state.scheduled_launch_day.pop(of_id, None)
            continue
        _launch_of_at_day(
            conn, of_id, horizon_start=scenario.horizon_start,
            day=day, actor=actor,
        )
        state.scheduled_launch_day.pop(of_id, None)


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
        _promote_frozen_candidates_to_ofs(
            conn, scenario, batch_id, result,
            state=state, use_smoothing=True, actor_prefix="flux",
        )

        hazards_by_day: dict[int, list] = {}
        for h in scenario.hazards:
            hazards_by_day.setdefault(h.day, []).append(h)

        for day in range(1, scenario.horizon_days + 1):
            _receive_due_purchase_orders(conn, scenario, day)
            # L9.4 : lance les OFs lissés du jour
            _launch_scheduled_ofs(conn, scenario, state, result, day,
                                  actor="flux.mes")
            # FLUX : sans régulation événementielle, replan APS sur chaque aléa.
            for h in hazards_by_day.get(day, []):
                _apply_hazard(conn, scenario, h, state, result, DOCTRINE_FLUX)
                if h.kind == HAZARD_URGENT_ORDER:
                    compute_candidates(conn)
                    result.aps_recalculations += 1
                    p1 = run_p1_promotion(conn, actor="flux.p1")
                    for plan in p1.ofs_created:
                        _arbitrate_of_routing(conn, plan.of_id, result)
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
                conn, scenario, day, state, result, rng, actor="flux.mes",
            )
            _decay_breakdowns(state)
            _measure_wip(conn, result, day)
    return result


# ----------------------------------------------------------------------
# Runner : doctrine EVENT (V3)
# ----------------------------------------------------------------------


def run_event_doctrine(
    scenario: Scenario, db_path: Path, *,
    fixtures_dir: Path = DEFAULT_FIXTURES_DIR,
    parameter_overrides: dict[str, float] | None = None,
    doctrine_override: str | None = None,
) -> RunResult:
    """Doctrine V3 : V1+V2 + événements attendus, matching, dual tolerance, causes, mémoire.

    `parameter_overrides` (L8.3) : dict de paramètres global à injecter en lieu
    et place des défauts, e.g. seuils filtre dual appris par la boucle longue.

    `doctrine_override` : si fourni, surcharge l'identifiant de
    doctrine porté par le RunResult et passé à `_apply_hazard`.
    Utilisé par le wrapper BCE (event_bce) pour activer le hook
    cybernétique tout en réutilisant ce runner.
    """
    doctrine = doctrine_override or DOCTRINE_EVENT
    _bootstrap_db(scenario, db_path, fixtures_dir)
    result = RunResult(
        doctrine=doctrine, scenario_name=scenario.name,
        db_path=db_path, seed=scenario.seed,
    )
    rng = random.Random(scenario.seed)
    state = HazardState()

    defaults = {
        "matching_time_tolerance_minutes": 1440.0,
        "cpm_margin_minutes": 240.0,
        "tolerance_threshold_watch": 0.20,
        "tolerance_threshold_correct_local": 0.50,
        "tolerance_threshold_replan_local": 1.00,
        "tolerance_threshold_escalate": 2.00,
        "tolerance_threshold_replan_global": 3.50,
    }
    # Pour les pilotages BCE : profil doctrinal CONSERVATIVE qui
    # favorise l'absorption (N1/N2) plutôt que la replanification
    # (N3/N4). Cohérent avec la doctrine cybernétique : moins de
    # nervosité de planning grâce à la chaîne MACRS+Delta.
    from pilotage_flux.comparative.bce_wire import (
        get_tolerance_defaults_for_doctrine,
    )
    bce_defaults = get_tolerance_defaults_for_doctrine(doctrine)
    if bce_defaults:
        defaults.update(bce_defaults)
    if parameter_overrides:
        defaults.update(parameter_overrides)

    with db_session(db_path) as conn:
        for name, value in defaults.items():
            conn.execute(
                "INSERT INTO parameters (scope, scope_ref, name, value_num) "
                "VALUES ('global', NULL, ?, ?)",
                (name, float(value)),
            )
        batch_id = _freeze_initial_contract(conn, scenario, result)
        result.batch_id = batch_id
        _promote_frozen_candidates_to_ofs(
            conn, scenario, batch_id, result,
            state=state, use_smoothing=True, actor_prefix="event",
        )
        # Wiring zone négociable → grain opération (Goldilocks #5)
        # Active uniquement pour les doctrines BCE ; sinon no-op.
        from pilotage_flux.comparative.bce_wire import (
            bce_distribute_pcs_after_freeze,
        )
        pc_info = bce_distribute_pcs_after_freeze(
            conn, batch_id, doctrine,
        )
        if pc_info is not None:
            result.notes.append(
                f"BCE PCs: via={pc_info['pcs_via']}, "
                f"n_pcs={pc_info['n_pcs']}, n_ofs={pc_info['n_ofs']}"
            )
        generate_expected_from_batch(conn, batch_id)

        hazards_by_day: dict[int, list] = {}
        for h in scenario.hazards:
            hazards_by_day.setdefault(h.day, []).append(h)

        for day in range(1, scenario.horizon_days + 1):
            _receive_due_purchase_orders(conn, scenario, day)
            # L9.4 : lance les OFs lissés du jour
            _launch_scheduled_ofs(conn, scenario, state, result, day,
                                  actor="event.mes")
            for h in hazards_by_day.get(day, []):
                _apply_hazard(conn, scenario, h, state, result, doctrine)
                # L8.1.c — EVENT doctrine : V3 crée les OFs pour servir la
                # demande (équité de production avec OF/FLUX) mais ne compte
                # un APS replan que pour le 1er urgent. Les suivants sont
                # absorbés via insertion locale dans la tranche gelée — pas
                # de coût de nervosité globale.
                if h.kind == HAZARD_URGENT_ORDER:
                    state.urgent_seen_count += 1
                    compute_candidates(conn)
                    if state.urgent_seen_count == 1:
                        result.aps_recalculations += 1
                    else:
                        # Absorption locale : pas de nervosité globale.
                        result.corrective_actions_applied.append({
                            "day": day,
                            "decision_id": None,
                            "action_level": "absorb_urgent",
                            "effect": "urgent_absorbed_no_aps_replan",
                            "urgent_count": state.urgent_seen_count,
                        })
                    p1 = run_p1_promotion(conn, actor="event.p1")
                    for plan in p1.ofs_created:
                        _arbitrate_of_routing(conn, plan.of_id, result)
                        result.of_created_day[plan.of_id] = day
                        _launch_of_at_day(
                            conn, plan.of_id,
                            horizon_start=scenario.horizon_start,
                            day=day, actor="event.mes",
                        )
            _advance_one_day(
                conn, scenario, day, state, result, rng, actor="event.mes",
            )
            _decay_breakdowns(state)
            _measure_wip(conn, result, day)

            # Détection continue : match → causes → décision dual tolérance
            match_actuals_to_expected(conn, batch_id)
            for d in list_deviations(conn):
                if not d.is_absorbed:
                    attach_causes_to_deviation(conn, d.deviation_id)
            evaluate_all_open_deviations(conn, batch_id=batch_id)
            # L5.2 + L8.1 : action corrective sur la réalité physique
            _apply_corrective_actions(conn, scenario, state, result, day)

        # Capture mémoire P4 sur les OF clôturés
        closed = conn.execute(
            "SELECT of_id FROM manufacturing_orders WHERE status = 'closed' "
            "ORDER BY of_id ASC"
        ).fetchall()
        for row in closed:
            capture_recipe(conn, of_id=row["of_id"], outcome="success")
    return result


# ----------------------------------------------------------------------
# L8.4 — Runner OF+EVENT : V0 OF-driven + couche event sourcing,
# SANS contractualisation flux. Permet d'isoler l'apport propre du flux
# (en comparant EVENT vs OF+EVENT) et l'apport propre de l'event sourcing
# (en comparant OF+EVENT vs OF).
# ----------------------------------------------------------------------


def _create_virtual_batch_for_of(
    conn: sqlite3.Connection, scenario: Scenario
) -> str:
    """Crée une freeze_batch virtuelle pour porter les expected_events en
    mode OF+EVENT (où il n'y a pas de contrat ni de tranche réelle).

    Cette tranche est un véhicule technique pour respecter le FK
    `expected_events.batch_id REFERENCES freeze_batches`, sans modifier
    la sémantique : pas de contrat lié, decision = 'OF_VIRTUAL'.
    """
    from datetime import date, timedelta

    horizon_start_dt = date.fromisoformat(scenario.horizon_start)
    horizon_end_dt = horizon_start_dt + timedelta(days=scenario.horizon_days)
    batch_id = "FZ-OF-VIRTUAL"
    conn.execute(
        """
        INSERT INTO freeze_batches
            (batch_id, horizon_start, horizon_end, status, decision,
             total_quantity, contract_count, candidate_count, explanation)
        VALUES (?, ?, ?, 'frozen', 'OF_VIRTUAL', 0, 0, 0, ?)
        """,
        (
            batch_id,
            scenario.horizon_start,
            horizon_end_dt.strftime("%Y-%m-%d"),
            "Tranche virtuelle pour porter expected_events en mode OF+EVENT (pas de contrat)",
        ),
    )
    return batch_id


def _generate_expected_from_ofs(
    conn: sqlite3.Connection,
    of_ids: list[str],
    horizon_start: str,
    batch_id: str,
) -> None:
    """Génère expected_events pour une liste d'OFs en mode OF (sans flux).

    L'« attendu » est calculé sans lissage : chaque OF est censé démarrer
    à horizon_start, puis enchaîner ses ops dans l'ordre de la gamme. C'est
    le référentiel d'attendu le plus naïf possible — la doctrine OF+EVENT
    ne dispose pas de mécanisme de lissage des lancements.
    """
    from datetime import datetime, timedelta

    try:
        base_dt = datetime.fromisoformat(horizon_start)
    except ValueError:
        base_dt = datetime.fromisoformat(horizon_start + "T00:00:00")

    for of_id in of_ids:
        of = conn.execute(
            """
            SELECT candidate_id, article_id, quantity
            FROM manufacturing_orders WHERE of_id = ?
            """,
            (of_id,),
        ).fetchone()
        if of is None or of["candidate_id"] is None:
            continue
        cand_id = of["candidate_id"]
        article_id = of["article_id"]
        qty = float(of["quantity"])
        ops = conn.execute(
            """
            SELECT sequence_idx, workstation_id, unit_time_min
            FROM routing_operations WHERE article_id = ?
            ORDER BY sequence_idx ASC
            """,
            (article_id,),
        ).fetchall()
        if not ops:
            continue

        cumul_min = 0.0
        for op in ops:
            op_minutes = float(op["unit_time_min"]) * qty
            start_dt = base_dt + timedelta(minutes=cumul_min)
            finish_dt = start_dt + timedelta(minutes=op_minutes)
            for evt_type, evt_dt in (
                ("op_start", start_dt),
                ("op_finish", finish_dt),
            ):
                conn.execute(
                    """
                    INSERT INTO expected_events
                        (batch_id, contract_id, candidate_id, event_type,
                         sequence_idx, workstation_id, expected_at, expected_qty)
                    VALUES (?, 'OF-VIRTUAL', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch_id, cand_id, evt_type,
                        int(op["sequence_idx"]), op["workstation_id"],
                        evt_dt.strftime("%Y-%m-%d %H:%M:%S"),
                        qty,
                    ),
                )
            cumul_min += op_minutes

        close_dt = base_dt + timedelta(minutes=cumul_min)
        conn.execute(
            """
            INSERT INTO expected_events
                (batch_id, contract_id, candidate_id, event_type,
                 expected_at, expected_qty)
            VALUES (?, 'OF-VIRTUAL', ?, 'of_close', ?, ?)
            """,
            (
                batch_id, cand_id,
                close_dt.strftime("%Y-%m-%d %H:%M:%S"),
                qty,
            ),
        )


def run_of_event_doctrine(
    scenario: Scenario, db_path: Path, *,
    fixtures_dir: Path = DEFAULT_FIXTURES_DIR,
    parameter_overrides: dict[str, float] | None = None,
    doctrine_override: str | None = None,
) -> RunResult:
    """Doctrine OF+EVENT (L8.4) : V0 OF-driven + event sourcing.

    Combinaison qui n'existe pas dans le cadrage stratifié original — on la
    construit pour isoler scientifiquement l'apport propre du flux. On a
    alors la matrice 2×2 : (flux ✗, event ✗)=OF, (flux ✓, event ✗)=FLUX,
    (flux ✗, event ✓)=OF+EVENT, (flux ✓, event ✓)=EVENT.

    Architecture : P1 direct (comme OF, pas de contrat) + tranche virtuelle
    pour porter les expected_events, puis le même pipeline matching → causes
    → dual tolérance → boucle physique → mémoire qu'EVENT.

    `doctrine_override` : si fourni, surcharge la doctrine portée
    par le RunResult et passée à `_apply_hazard` (utilisé par le
    wrapper BCE `of_event_bce`).
    """
    doctrine = doctrine_override or DOCTRINE_OF_EVENT
    _bootstrap_db(scenario, db_path, fixtures_dir)
    result = RunResult(
        doctrine=doctrine, scenario_name=scenario.name,
        db_path=db_path, seed=scenario.seed,
    )
    rng = random.Random(scenario.seed)
    state = HazardState()

    defaults = {
        "matching_time_tolerance_minutes": 1440.0,
        "cpm_margin_minutes": 240.0,
        "tolerance_threshold_watch": 0.20,
        "tolerance_threshold_correct_local": 0.50,
        "tolerance_threshold_replan_local": 1.00,
        "tolerance_threshold_escalate": 2.00,
        "tolerance_threshold_replan_global": 3.50,
    }
    # Pour les pilotages BCE : profil doctrinal CONSERVATIVE qui
    # favorise l'absorption (N1/N2) plutôt que la replanification
    # (N3/N4). Cohérent avec la doctrine cybernétique : moins de
    # nervosité de planning grâce à la chaîne MACRS+Delta.
    from pilotage_flux.comparative.bce_wire import (
        get_tolerance_defaults_for_doctrine,
    )
    bce_defaults = get_tolerance_defaults_for_doctrine(doctrine)
    if bce_defaults:
        defaults.update(bce_defaults)
    if parameter_overrides:
        defaults.update(parameter_overrides)

    with db_session(db_path) as conn:
        for name, value in defaults.items():
            conn.execute(
                "INSERT INTO parameters (scope, scope_ref, name, value_num) "
                "VALUES ('global', NULL, ?, ?)",
                (name, float(value)),
            )

        # P1 immédiat sur les SO initiaux (mode OF — pas de contrat)
        p1 = run_p1_promotion(conn, actor="of_event.p1")
        result.aps_recalculations += 1
        initial_of_ids: list[str] = []
        for plan in p1.ofs_created:
            _arbitrate_of_routing(conn, plan.of_id, result)
            result.of_created_day[plan.of_id] = 0
            _launch_of_at_day(
                conn, plan.of_id, horizon_start=scenario.horizon_start,
                day=0, actor="of_event.mes",
            )
            initial_of_ids.append(plan.of_id)

        # Tranche virtuelle + expected events pour la couche événementielle
        batch_id = _create_virtual_batch_for_of(conn, scenario)
        result.batch_id = batch_id
        _generate_expected_from_ofs(
            conn, initial_of_ids, scenario.horizon_start, batch_id
        )

        # Wiring zone négociable → grain opération (Goldilocks #5)
        # Pour OF+EVENT+BCE : tranche virtuelle → fallback per-OF.
        from pilotage_flux.comparative.bce_wire import (
            bce_distribute_pcs_after_freeze,
        )
        pc_info = bce_distribute_pcs_after_freeze(
            conn, batch_id, doctrine,
        )
        if pc_info is not None:
            result.notes.append(
                f"BCE PCs: via={pc_info['pcs_via']}, "
                f"n_pcs={pc_info['n_pcs']}, n_ofs={pc_info['n_ofs']}"
            )

        hazards_by_day: dict[int, list] = {}
        for h in scenario.hazards:
            hazards_by_day.setdefault(h.day, []).append(h)

        for day in range(1, scenario.horizon_days + 1):
            _receive_due_purchase_orders(conn, scenario, day)
            for h in hazards_by_day.get(day, []):
                _apply_hazard(conn, scenario, h, state, result, doctrine)
                if h.kind == HAZARD_URGENT_ORDER:
                    state.urgent_seen_count += 1
                    compute_candidates(conn)
                    if state.urgent_seen_count == 1:
                        result.aps_recalculations += 1
                    else:
                        # L8.1.c : absorption locale au-delà du 1er urgent
                        result.corrective_actions_applied.append({
                            "day": day,
                            "decision_id": None,
                            "action_level": "absorb_urgent",
                            "effect": "urgent_absorbed_no_aps_replan",
                            "urgent_count": state.urgent_seen_count,
                        })
                    p1 = run_p1_promotion(conn, actor="of_event.p1")
                    new_of_ids: list[str] = []
                    for plan in p1.ofs_created:
                        _arbitrate_of_routing(conn, plan.of_id, result)
                        result.of_created_day[plan.of_id] = day
                        _launch_of_at_day(
                            conn, plan.of_id,
                            horizon_start=scenario.horizon_start,
                            day=day, actor="of_event.mes",
                        )
                        new_of_ids.append(plan.of_id)
                    # Étend les expected_events à ces nouveaux OFs
                    if new_of_ids:
                        _generate_expected_from_ofs(
                            conn, new_of_ids,
                            scenario.horizon_start, batch_id,
                        )
            _advance_one_day(
                conn, scenario, day, state, result, rng, actor="of_event.mes",
            )
            _decay_breakdowns(state)
            _measure_wip(conn, result, day)

            # Couche événementielle V3 : match → causes → décision dual
            match_actuals_to_expected(conn, batch_id)
            for d in list_deviations(conn):
                if not d.is_absorbed:
                    attach_causes_to_deviation(conn, d.deviation_id)
            evaluate_all_open_deviations(conn, batch_id=batch_id)
            # L5.2 + L8.1 : boucle physique corrective
            _apply_corrective_actions(conn, scenario, state, result, day)

        # Capture mémoire P4 sur les OF clôturés
        closed = conn.execute(
            "SELECT of_id FROM manufacturing_orders WHERE status = 'closed' "
            "ORDER BY of_id ASC"
        ).fetchall()
        for row in closed:
            capture_recipe(conn, of_id=row["of_id"], outcome="success")
    return result


# ----------------------------------------------------------------------
# §7.1 — Doctrine OF_MILP : OF + planification CP-SAT au jour 0
# ----------------------------------------------------------------------


def run_of_milp_doctrine(
    scenario: Scenario, db_path: Path,
    *, fixtures_dir: Path = DEFAULT_FIXTURES_DIR,
) -> RunResult:
    """§7.1 — Variante OF avec planification globale CP-SAT.

    Identique à OF (pas de flux, pas d'event sourcing) mais utilise
    `milp_scheduler.compute_milp_launch_days` pour étaler les OFs sur
    l'horizon au jour 0, au lieu de tout lancer immédiatement.

    Baseline plus sophistiquée que SLACK+FIFO, comparée aux 4 doctrines
    pour valider l'absence de biais d'implémentation côté référentiel.
    """
    from pilotage_flux.comparative.milp_scheduler import (
        compute_milp_launch_days,
    )
    _bootstrap_db(scenario, db_path, fixtures_dir)
    result = RunResult(
        doctrine=DOCTRINE_OF_MILP, scenario_name=scenario.name,
        db_path=db_path, seed=scenario.seed,
    )
    rng = random.Random(scenario.seed)
    state = HazardState()

    with db_session(db_path) as conn:
        # Jour 0 : P1 immédiat (pas de contrat de flux)
        p1 = run_p1_promotion(conn, actor="of_milp.p1")
        result.aps_recalculations += 1
        for plan in p1.ofs_created:
            _arbitrate_of_routing(conn, plan.of_id, result)

        # Résolution CP-SAT après création des OFs
        milp_res = compute_milp_launch_days(
            conn, horizon_days=scenario.horizon_days,
        )
        result.notes.append(
            f"milp_status={milp_res.status} "
            f"solve_time={milp_res.solve_time_sec:.2f}s "
            f"obj={milp_res.objective_value:.0f}"
        )

        # Applique les launch_day calculés
        for plan in p1.ofs_created:
            scheduled = milp_res.launch_day_by_of.get(plan.of_id, 0)
            state.scheduled_launch_day[plan.of_id] = scheduled
            result.of_created_day[plan.of_id] = scheduled
            if scheduled == 0:
                _launch_of_at_day(
                    conn, plan.of_id, horizon_start=scenario.horizon_start,
                    day=0, actor="of_milp.mes",
                )

        hazards_by_day: dict[int, list] = {}
        for h in scenario.hazards:
            hazards_by_day.setdefault(h.day, []).append(h)

        for day in range(1, scenario.horizon_days + 1):
            _receive_due_purchase_orders(conn, scenario, day)
            # Lance les OFs dont le jour MILP est arrivé
            for of_id, sched_day in list(state.scheduled_launch_day.items()):
                if sched_day == day and of_id not in result.of_created_day:
                    pass  # Géré ci-dessous, of déjà créé au jour 0
                if sched_day == day:
                    _launch_of_at_day(
                        conn, of_id, horizon_start=scenario.horizon_start,
                        day=day, actor="of_milp.mes",
                    )
                    state.scheduled_launch_day.pop(of_id, None)
            # Aléas — replan global comme OF (pas de tolérance dual)
            for h in hazards_by_day.get(day, []):
                _apply_hazard(conn, scenario, h, state, result, DOCTRINE_OF)
                if h.kind == HAZARD_URGENT_ORDER:
                    new = run_p1_promotion(conn, actor="of_milp.p1")
                    result.aps_recalculations += 1
                    for plan in new.ofs_created:
                        _arbitrate_of_routing(conn, plan.of_id, result)
                        result.of_created_day[plan.of_id] = day
                        _launch_of_at_day(
                            conn, plan.of_id,
                            horizon_start=scenario.horizon_start,
                            day=day, actor="of_milp.mes",
                        )
                else:
                    compute_candidates(conn)
                    result.aps_recalculations += 1
            _advance_one_day(
                conn, scenario, day, state, result, rng, actor="of_milp.mes",
            )
            _decay_breakdowns(state)
            _measure_wip(conn, result, day)
    return result


# ----------------------------------------------------------------------
# Point 2 paper — mécanisme de rejet de SO (§7.2)
# ----------------------------------------------------------------------


DEFAULT_LATE_THRESHOLD_DAYS = 14
# 14 jours = par défaut, un client cancellait une commande dont la
# livraison dépasse de 2 semaines la due_date.


def _evaluate_rejections(
    db_path: Path, scenario: Scenario, result: RunResult,
    *, late_threshold_days: int | None = None,
) -> None:
    """Marque comme `cancelled` les SOs dont la livraison réelle a
    dépassé `due_date + late_threshold_days`.

    Lève un proxy de **disponibilité réelle** dans le modèle, à la
    différence du KPI of_closed/of_total qui reste toujours élevé
    (le simulateur ne refuse jamais une commande).

    Si `late_threshold_days` est fourni explicitement, il prime sur
    la valeur en DB. Sinon le seuil est lu depuis `parameters` (clé
    `late_threshold_days`), avec fallback sur DEFAULT_LATE_THRESHOLD_DAYS.
    """
    from datetime import datetime, timedelta
    from pilotage_flux.db import db_session
    from pilotage_flux.parameters import get_num

    with db_session(db_path) as conn:
        if late_threshold_days is not None:
            threshold = int(late_threshold_days)
        else:
            threshold = int(
                get_num(conn, scope="global", scope_ref=None,
                        name="late_threshold_days",
                        default=DEFAULT_LATE_THRESHOLD_DAYS) or DEFAULT_LATE_THRESHOLD_DAYS
            )
        base = datetime.fromisoformat(scenario.horizon_start)
        horizon_end = base + timedelta(days=scenario.horizon_days)

        rows = conn.execute(
            "SELECT sales_order_id, due_date, status FROM sales_orders"
        ).fetchall()
        for r in rows:
            if r["status"] == "cancelled":
                continue
            try:
                due = datetime.fromisoformat(r["due_date"])
            except (ValueError, TypeError):
                continue
            deadline = due + timedelta(days=threshold)
            # Si l'horizon est plus court que la deadline, on ne peut
            # pas juger → on attend
            if horizon_end < deadline:
                continue

            # Cette SO est-elle livrée avant deadline ?
            delivered = conn.execute(
                """
                SELECT 1 FROM manufacturing_orders m
                JOIN candidate_orders c ON c.candidate_id = m.candidate_id
                WHERE c.sales_order_id = ?
                  AND m.status = 'closed'
                  AND m.actual_end IS NOT NULL
                  AND m.actual_end <= ?
                LIMIT 1
                """,
                (r["sales_order_id"], deadline.isoformat()),
            ).fetchone()
            if delivered is None:
                conn.execute(
                    """
                    UPDATE sales_orders
                    SET status = 'cancelled',
                        rejected_at = ?,
                        rejection_reason = 'late_beyond_threshold'
                    WHERE sales_order_id = ?
                    """,
                    (horizon_end.isoformat(), r["sales_order_id"]),
                )


# ----------------------------------------------------------------------
# Dispatch
# ----------------------------------------------------------------------


from pilotage_flux.comparative.bce_wire import (
    run_event_bce_doctrine,
    run_of_event_bce_doctrine,
)
from pilotage_flux.comparative.scenario import (
    DOCTRINE_EVENT_BCE,
    DOCTRINE_OF_EVENT_BCE,
)

_DISPATCH = {
    DOCTRINE_OF: run_of_doctrine,
    DOCTRINE_FLUX: run_flux_doctrine,
    DOCTRINE_OF_EVENT: run_of_event_doctrine,
    DOCTRINE_EVENT: run_event_doctrine,
    DOCTRINE_OF_MILP: run_of_milp_doctrine,
    DOCTRINE_OF_EVENT_BCE: run_of_event_bce_doctrine,
    DOCTRINE_EVENT_BCE: run_event_bce_doctrine,
}


def run_doctrine(
    scenario: Scenario,
    doctrine: str,
    db_path: Path,
    *,
    fixtures_dir: Path = DEFAULT_FIXTURES_DIR,
    evaluate_rejections: bool = True,
    late_threshold_days: int | None = None,
    param_overrides: dict | None = None,
) -> RunResult:
    """Lance la doctrine demandée.

    `evaluate_rejections` (Point 2 paper, défaut True) déclenche le
    post-processing qui marque comme `cancelled` les SOs dont la
    livraison dépasse `due_date + late_threshold_days`. Permet la
    mesure de disponibilité réelle.

    `late_threshold_days` (Point 2 paper) override la valeur par
    défaut/DB. Utile pour profils stricts (0 = livraison ontime).

    `param_overrides` (Point 3 paper) : dict
    {(scope, scope_ref, name): value_num} appliqué après le bootstrap
    de la DB et avant la simulation. Permet de varier directement
    les paramètres data-driven (buffer DBR, seuils Little, coûts, etc.)
    sans modifier les fixtures.
    """
    if doctrine not in _DISPATCH:
        raise ValueError(
            f"Doctrine inconnue : {doctrine!r} (dispatch : {list(_DISPATCH)})"
        )
    global _PENDING_PARAM_OVERRIDES
    previous_overrides = _PENDING_PARAM_OVERRIDES
    try:
        if param_overrides:
            _PENDING_PARAM_OVERRIDES = param_overrides
        result = _DISPATCH[doctrine](
            scenario, db_path, fixtures_dir=fixtures_dir,
        )
    finally:
        _PENDING_PARAM_OVERRIDES = previous_overrides
    if evaluate_rejections:
        _evaluate_rejections(
            db_path, scenario, result,
            late_threshold_days=late_threshold_days,
        )
    return result
