"""V12.2 — CP-SAT dynamique zone-aware.

Extension de `comparative/milp_scheduler.py` :

  - Le solveur initial calcule un plan global au jour 0 (one-shot)
  - V12.2 cible **uniquement la zone négociable** identifiée par
    `zone_resolver.resolve_negotiable_zone()`
  - Le résultat est une **proposition de re-plan** (ProposedReplan)
    qui peut être :
      - appliquée immédiatement (L2 autonome)
      - enqueue pour validation (L3/L4 via V12.3.dispatcher)

Le solveur respecte la freeze window : les OFs dont le launch_day
est dans [t, t+freeze_window[ ne sont JAMAIS déplacés.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

try:
    from ortools.sat.python import cp_model
    _HAS_ORTOOLS = True
except ImportError:
    _HAS_ORTOOLS = False

from pilotage_flux.cybernetic.optimization.zone_resolver import NegotiableZone


@dataclass
class ProposedReplan:
    """Proposition de replan produite par CP-SAT dynamique.

    Contient les changements proposés (deltas) + des indicateurs
    d'amplitude pour permettre une approbation informée.
    """

    target_zone: NegotiableZone
    new_launch_day_by_of: dict[str, int] = field(default_factory=dict)
    deltas: dict[str, int] = field(default_factory=dict)
    # of_id -> nouveau_jour - ancien_jour (positif = repoussé)
    n_ofs_moved: int = 0
    max_delta_days: int = 0
    total_delta_days: int = 0
    status: str = "feasible"
    solve_time_sec: float = 0.0
    objective_value: float = 0.0


def propose_dynamic_replan(
    conn: sqlite3.Connection,
    zone: NegotiableZone,
    *,
    timeout_sec: float = 10.0,
) -> ProposedReplan:
    """Re-plan les OFs dans la zone négociable via CP-SAT.

    Les OFs dans la freeze window sont **interdits** de déplacement
    (contrainte égalité sur leur jour actuel).

    Le solveur minimise le total des décalages (tardiveté + makespan
    pondéré).
    """
    if zone.is_empty:
        return ProposedReplan(
            target_zone=zone,
            status="empty_zone",
        )

    # Récupère le plan actuel pour les OFs concernés
    of_ids = list(zone.of_ids_in_zone)
    if not of_ids:
        return ProposedReplan(target_zone=zone, status="no_of_in_zone")

    placeholders = ",".join("?" * len(of_ids))
    rows = conn.execute(
        f"""
        SELECT m.of_id, m.planned_start,
               COALESCE(so.due_date, c.latest_end) AS due_date
        FROM manufacturing_orders m
        LEFT JOIN candidate_orders c ON c.candidate_id = m.candidate_id
        LEFT JOIN sales_orders so ON so.sales_order_id = c.sales_order_id
        WHERE m.of_id IN ({placeholders})
        """,
        tuple(of_ids),
    ).fetchall()
    if not rows:
        return ProposedReplan(target_zone=zone, status="no_data")

    # Convert planned_start → jour logique
    from datetime import datetime
    horizon_meta = conn.execute(
        "SELECT value FROM run_metadata WHERE key = 'horizon_start'"
    ).fetchone()
    base = (
        datetime.fromisoformat(horizon_meta["value"])
        if horizon_meta else datetime.utcnow()
    )
    current_day_by_of: dict[str, int] = {}
    due_day_by_of: dict[str, int] = {}
    for r in rows:
        if r["planned_start"]:
            try:
                d = (datetime.fromisoformat(r["planned_start"]) - base).days
                current_day_by_of[r["of_id"]] = d
            except (ValueError, TypeError):
                current_day_by_of[r["of_id"]] = zone.freeze_end_day
        else:
            current_day_by_of[r["of_id"]] = zone.freeze_end_day
        if r["due_date"]:
            try:
                d = (datetime.fromisoformat(r["due_date"]) - base).days
                due_day_by_of[r["of_id"]] = d
            except (ValueError, TypeError):
                due_day_by_of[r["of_id"]] = zone.horizon_end_day
        else:
            due_day_by_of[r["of_id"]] = zone.horizon_end_day

    # Durées par OF (somme des opérations en jours, min 1)
    duration_days: dict[str, int] = {}
    for of_id in of_ids:
        ops = conn.execute(
            """
            SELECT SUM(unit_time_min) AS total_min
            FROM order_operations WHERE of_id = ?
            """,
            (of_id,),
        ).fetchone()
        total_min = int(ops["total_min"] or 60)
        duration_days[of_id] = max(1, (total_min + 479) // 480)

    # Fallback heuristique si OR-Tools manquant
    if not _HAS_ORTOOLS:
        return _fallback_slack(zone, of_ids, current_day_by_of,
                                due_day_by_of, duration_days)

    # Modèle CP-SAT
    import time
    t0 = time.time()
    model = cp_model.CpModel()
    launch_day: dict[str, Any] = {}
    for of_id in of_ids:
        # Domaine : [freeze_end_day, horizon_end_day[
        launch_day[of_id] = model.NewIntVar(
            zone.freeze_end_day, zone.horizon_end_day - 1,
            f"launch_{of_id}",
        )

    # Contrainte tardiveté
    tardiness_vars = []
    for of_id in of_ids:
        finish_day = launch_day[of_id] + duration_days[of_id]
        tdy = model.NewIntVar(0, zone.horizon_end_day * 2, f"tdy_{of_id}")
        model.AddMaxEquality(
            tdy, [finish_day - due_day_by_of[of_id], 0],
        )
        tardiness_vars.append(tdy)

    # Contrainte de capacité simplifiée : max N OFs commençant le même jour
    n = len(of_ids)
    horizon_width = max(1, zone.horizon_end_day - zone.freeze_end_day)
    max_per_day = max(2, n // max(1, horizon_width // 2))
    for day in range(zone.freeze_end_day, zone.horizon_end_day):
        indicators = []
        for of_id in of_ids:
            ind = model.NewBoolVar(f"ind_{of_id}_d{day}")
            model.Add(launch_day[of_id] == day).OnlyEnforceIf(ind)
            model.Add(launch_day[of_id] != day).OnlyEnforceIf(ind.Not())
            indicators.append(ind)
        model.Add(sum(indicators) <= max_per_day)

    # Objectif : minimiser 10 × tardiveté + somme |delta_vs_current|
    total_delta = []
    for of_id in of_ids:
        d = model.NewIntVar(0, zone.horizon_end_day, f"delta_{of_id}")
        model.AddAbsEquality(
            d, launch_day[of_id] - current_day_by_of[of_id],
        )
        total_delta.append(d)
    model.Minimize(10 * sum(tardiness_vars) + sum(total_delta))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = timeout_sec
    solver.parameters.num_search_workers = 4
    status_code = solver.Solve(model)

    if status_code == cp_model.OPTIMAL:
        status_str = "optimal"
    elif status_code == cp_model.FEASIBLE:
        status_str = "feasible"
    else:
        return _fallback_slack(zone, of_ids, current_day_by_of,
                                due_day_by_of, duration_days)

    new_launch: dict[str, int] = {}
    deltas: dict[str, int] = {}
    n_moved = 0
    max_d = 0
    total_d = 0
    for of_id in of_ids:
        nv = int(solver.Value(launch_day[of_id]))
        new_launch[of_id] = nv
        delta = nv - current_day_by_of[of_id]
        deltas[of_id] = delta
        if delta != 0:
            n_moved += 1
            max_d = max(max_d, abs(delta))
            total_d += abs(delta)

    return ProposedReplan(
        target_zone=zone,
        new_launch_day_by_of=new_launch,
        deltas=deltas,
        n_ofs_moved=n_moved,
        max_delta_days=max_d,
        total_delta_days=total_d,
        status=status_str,
        solve_time_sec=time.time() - t0,
        objective_value=solver.ObjectiveValue(),
    )


def _fallback_slack(
    zone: NegotiableZone,
    of_ids: list[str],
    current_day_by_of: dict[str, int],
    due_day_by_of: dict[str, int],
    duration_days: dict[str, int],
) -> ProposedReplan:
    """Fallback heuristique SLACK quand OR-Tools indispo / infaisable.

    Étale les OFs uniformément dans la zone négociable, triés par
    slack = due_date - duration (les plus urgents en premier).
    """
    sorted_ofs = sorted(
        of_ids,
        key=lambda oid: due_day_by_of[oid] - duration_days[oid],
    )
    n = len(sorted_ofs)
    width = max(1, zone.horizon_end_day - zone.freeze_end_day)
    new_launch: dict[str, int] = {}
    deltas: dict[str, int] = {}
    n_moved = 0
    max_d = 0
    total_d = 0
    for idx, oid in enumerate(sorted_ofs):
        # Spread linéairement dans la zone négociable
        nv = zone.freeze_end_day + (idx * width) // max(1, n)
        new_launch[oid] = nv
        delta = nv - current_day_by_of[oid]
        deltas[oid] = delta
        if delta != 0:
            n_moved += 1
            max_d = max(max_d, abs(delta))
            total_d += abs(delta)
    return ProposedReplan(
        target_zone=zone,
        new_launch_day_by_of=new_launch,
        deltas=deltas,
        n_ofs_moved=n_moved,
        max_delta_days=max_d,
        total_delta_days=total_d,
        status="fallback_slack",
        solve_time_sec=0.0,
        objective_value=0.0,
    )
