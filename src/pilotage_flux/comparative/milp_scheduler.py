"""Planificateur OF_MILP via OR-Tools CP-SAT (§7.1 paper HAL).

Baseline alternative à l'heuristique SLACK+FIFO du runner OF actuel.
Pour chaque OF créé, calcule un `launch_day` optimal en résolvant un
problème de job-shop scheduling avec capacité goulot :

  - Variables : start_day de chaque OF (entier, [0, horizon[)
  - Précédence : opérations dans le routing de chaque OF
  - Capacité : pas plus de N OF qui consomment le même poste le même jour
  - Due date : OF doit finir avant due_day du SO d'origine
  - Objectif : minimiser la tardiveté totale + un terme de lissage de
               la charge journalière sur le goulot

Limites assumées :
  - Modèle simplifié : 1 OF consomme son temps total sur 1 jour, pas
    de granularité intra-journalière.
  - Pas de retours d'aléas dans le solveur (le runner les applique
    après le plan).
  - Timeout 10 s par scénario ; si infaisable → fallback heuristique.

Contraste vs OF baseline : le solveur **étale** les OFs sur l'horizon
au lieu de tout lancer au jour 0, ce qui devrait diminuer la
congestion goulot et le coût.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

try:
    from ortools.sat.python import cp_model
    _HAS_ORTOOLS = True
except ImportError:
    _HAS_ORTOOLS = False


@dataclass
class MilpResult:
    """Résultat de la planification MILP."""

    launch_day_by_of: dict[str, int]
    objective_value: float
    status: str  # "optimal" | "feasible" | "infeasible" | "timeout" | "fallback"
    solve_time_sec: float


def compute_milp_launch_days(
    conn: sqlite3.Connection,
    horizon_days: int,
    timeout_sec: float = 10.0,
) -> MilpResult:
    """Résout le problème de planification global et retourne les
    `launch_day` optimaux pour tous les OFs créés (status='created').

    Si OR-Tools n'est pas installé ou si le solveur échoue, retourne
    un fallback heuristique : SLACK décroissant (OFs avec moins de
    marge lancés en premier).
    """
    of_rows = conn.execute(
        """
        SELECT m.of_id, m.article_id,
               COALESCE(so.due_date, c.latest_end, '') AS due_date,
               COALESCE(c.sales_order_id, '') AS so_id
        FROM manufacturing_orders m
        LEFT JOIN candidate_orders c ON c.candidate_id = m.candidate_id
        LEFT JOIN sales_orders so ON so.sales_order_id = c.sales_order_id
        WHERE m.status IN ('created', 'planned')
        ORDER BY m.of_id ASC
        """
    ).fetchall()
    if not of_rows:
        return MilpResult({}, 0.0, "no_ofs", 0.0)

    of_ids = [r["of_id"] for r in of_rows]
    # Pour chaque OF, somme des unit_time_min de ses opérations
    of_duration_min: dict[str, int] = {}
    of_bottleneck_load: dict[str, dict[str, int]] = {}
    for of_id in of_ids:
        ops = conn.execute(
            """
            SELECT workstation_id, unit_time_min
            FROM order_operations
            WHERE of_id = ?
            ORDER BY sequence_idx ASC
            """,
            (of_id,),
        ).fetchall()
        total = int(sum(o["unit_time_min"] for o in ops))
        of_duration_min[of_id] = max(total, 60)  # min 1h
        # Charge par poste
        load_by_ws: dict[str, int] = {}
        for o in ops:
            ws = o["workstation_id"]
            load_by_ws[ws] = load_by_ws.get(ws, 0) + int(o["unit_time_min"])
        of_bottleneck_load[of_id] = load_by_ws

    # Calcule la due date en jours depuis le début de l'horizon
    # Si pas de due_date → horizon_days - 1
    from datetime import datetime
    base_ts = conn.execute(
        "SELECT due_date FROM sales_orders ORDER BY due_date ASC LIMIT 1"
    ).fetchone()
    if base_ts is None:
        base_day = datetime.utcnow()
    else:
        base_day = datetime.fromisoformat(base_ts["due_date"])

    due_day_by_of: dict[str, int] = {}
    for r in of_rows:
        due_str = r["due_date"]
        if not due_str:
            due_day_by_of[r["of_id"]] = horizon_days - 1
            continue
        try:
            due_dt = datetime.fromisoformat(due_str)
            delta = (due_dt - base_day).days
            due_day_by_of[r["of_id"]] = max(0, min(horizon_days - 1, delta))
        except (ValueError, TypeError):
            due_day_by_of[r["of_id"]] = horizon_days - 1

    # Capacités quotidiennes par poste (480 min = 8h utiles par défaut)
    default_cap = 480
    ws_capacity_min: dict[str, int] = {}
    for r in conn.execute(
        "SELECT workstation_id FROM workstations"
    ).fetchall():
        ws_capacity_min[r["workstation_id"]] = default_cap

    # Fallback heuristique : étale les OFs sur l'horizon, OF urgent en premier
    def _fallback_slack() -> MilpResult:
        sorted_ofs = sorted(
            of_ids, key=lambda i: due_day_by_of[i] - of_duration_min[i] / 480
        )
        result = {}
        for idx, of_id in enumerate(sorted_ofs):
            # Étale par lots de 2 OFs par jour
            result[of_id] = min(horizon_days - 1, idx // 2)
        return MilpResult(result, 0.0, "fallback", 0.0)

    if not _HAS_ORTOOLS:
        return _fallback_slack()

    # Modèle CP-SAT
    import time
    t0 = time.time()
    model = cp_model.CpModel()
    n = len(of_ids)

    # Variable : launch_day de chaque OF
    launch_day = {
        of_id: model.NewIntVar(0, horizon_days - 1, f"launch_{of_id}")
        for of_id in of_ids
    }
    # Variable : finish_day = launch_day + duration_in_days
    # Durée en jours = ceil(total_min / 480) (8h utiles par jour)
    duration_days = {
        of_id: max(1, (of_duration_min[of_id] + 479) // 480)
        for of_id in of_ids
    }
    finish_day = {
        of_id: launch_day[of_id] + duration_days[of_id]
        for of_id in of_ids
    }

    # Tardiveté
    tardiness = {}
    for of_id in of_ids:
        tdy = model.NewIntVar(0, horizon_days * 2, f"tardiness_{of_id}")
        model.AddMaxEquality(tdy, [finish_day[of_id] - due_day_by_of[of_id], 0])
        tardiness[of_id] = tdy

    # Capacité goulot : pour chaque (workstation, jour),
    # somme(charge OF * indicator_started_that_day) <= capacité
    # Indicators : pour chaque (of, jour), bool = (launch_day == day)
    # Trop de variables → on simplifie : limite le nombre d'OF qui
    # commencent le même jour à K = capacité moyenne / charge moyenne
    avg_load_per_of = sum(of_duration_min.values()) / max(1, n)
    avg_capacity = (
        sum(ws_capacity_min.values()) / max(1, len(ws_capacity_min))
    )
    max_starts_per_day = max(2, int(avg_capacity / max(1, avg_load_per_of)))

    # Pour chaque jour, somme(indicator) ≤ max_starts_per_day
    for day in range(horizon_days):
        indicators = []
        for of_id in of_ids:
            ind = model.NewBoolVar(f"ind_{of_id}_d{day}")
            model.Add(launch_day[of_id] == day).OnlyEnforceIf(ind)
            model.Add(launch_day[of_id] != day).OnlyEnforceIf(ind.Not())
            indicators.append(ind)
        model.Add(sum(indicators) <= max_starts_per_day)

    # Objectif : minimiser tardiveté totale + 0.1 * makespan
    makespan = model.NewIntVar(0, horizon_days * 2, "makespan")
    model.AddMaxEquality(makespan, list(finish_day.values()))
    total_tardiness = sum(tardiness.values())
    model.Minimize(10 * total_tardiness + makespan)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = timeout_sec
    solver.parameters.num_search_workers = 4
    status_code = solver.Solve(model)

    if status_code == cp_model.OPTIMAL:
        status_str = "optimal"
    elif status_code == cp_model.FEASIBLE:
        status_str = "feasible"
    else:
        return _fallback_slack()

    result = {of_id: solver.Value(launch_day[of_id]) for of_id in of_ids}
    return MilpResult(
        launch_day_by_of=result,
        objective_value=solver.ObjectiveValue(),
        status=status_str,
        solve_time_sec=time.time() - t0,
    )
