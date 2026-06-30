"""Arbitrage routing linéaire / parallèle / hybride (L11.2).

À la création d'un OF, on choisit pour chaque opération le poste qui
minimise son EFT (earliest finish time), en tenant compte :

  - Du poste préféré (routing principal : `routing_operations`).
  - Des alternatives déclarées (`routing_alternatives`).
  - De la charge actuelle (sum unit_time × qty pour les ops 'pending'
    déjà allouées à chaque poste).

Stratégies couvertes naturellement par ce choix :

  - **Linéaire** : tous les OFs suivent le routing principal (cas où les
    alternatives sont moins favorables — coût supérieur ou pas dispo).
  - **Parallèle** : un OF prend une alternative car le poste préféré est
    déjà saturé par d'autres OFs concurrents.
  - **Hybride** : certaines ops d'un même OF prennent l'alternative,
    d'autres restent sur le routing principal.

Politique data-driven via paramètres :
  - `routing_arbitrage_enabled` (default 1) : active/désactive l'arbitrage
  - `routing_arbitrage_min_savings_min` (default 30) : seuil minimum
    d'économie en minutes pour basculer (évite les micro-changements)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pilotage_flux.aps.routing_alternatives import list_alternatives_for
from pilotage_flux.parameters import get_num, workstation_capacity_factor


DEFAULT_MIN_SAVINGS_MIN = 30.0


@dataclass
class ArbitrageDecision:
    of_op_id: int
    sequence_idx: int
    original_workstation: str
    chosen_workstation: str
    original_eft: float
    chosen_eft: float
    savings_min: float
    strategy: str  # 'linear' | 'parallel' | 'hybrid'


def _current_pending_load_min(conn: sqlite3.Connection) -> dict[str, float]:
    """Charge actuelle en minutes par workstation : Σ(unit_time × qty) pour
    les ops en statut 'pending'."""
    rows = conn.execute(
        """
        SELECT oo.workstation_id,
               COALESCE(SUM(oo.unit_time_min * mo.quantity), 0) AS load_min
        FROM order_operations oo
        JOIN manufacturing_orders mo ON mo.of_id = oo.of_id
        WHERE oo.status = 'pending'
        GROUP BY oo.workstation_id
        """
    ).fetchall()
    return {r["workstation_id"]: float(r["load_min"]) for r in rows}


def _candidate_durations(
    conn: sqlite3.Connection,
    article_id: str,
    sequence_idx: int,
    preferred_ws: str,
    preferred_unit_time: float,
) -> list[tuple[str, float]]:
    """Liste (workstation_id, unit_time_min) candidats pour une op :
    le préféré + alternatives déclarées."""
    candidates: list[tuple[str, float]] = [(preferred_ws, preferred_unit_time)]
    for alt in list_alternatives_for(conn, article_id, sequence_idx):
        candidates.append((alt.workstation_id, alt.unit_time_min))
    # Dédoublonne (préférence au préféré)
    seen: set[str] = set()
    out: list[tuple[str, float]] = []
    for ws, ut in candidates:
        if ws in seen:
            continue
        seen.add(ws)
        out.append((ws, ut))
    return out


def arbitrate_routing_for_of(
    conn: sqlite3.Connection, of_id: str,
) -> list[ArbitrageDecision]:
    """Pour chaque op de l'OF, choisit le poste qui minimise l'EFT en tenant
    compte de la charge déjà allouée.

    Met à jour `order_operations.workstation_id` et `unit_time_min` quand
    une alternative est choisie. Renvoie la liste des décisions.

    L'arbitrage est ignoré si `routing_arbitrage_enabled = 0` dans parameters.
    """
    enabled = get_num(
        conn, scope="global", scope_ref=None,
        name="routing_arbitrage_enabled", default=1,
    )
    if enabled is not None and float(enabled) < 0.5:
        return []

    min_savings = float(
        get_num(
            conn, scope="global", scope_ref=None,
            name="routing_arbitrage_min_savings_min",
            default=DEFAULT_MIN_SAVINGS_MIN,
        ) or DEFAULT_MIN_SAVINGS_MIN
    )

    # Goldilocks — implantation forcée via routing_strategy_code :
    #   0 = hybrid (défaut)  1 = linear forcé  2 = parallel forcé
    forced_code = get_num(
        conn, scope="global", scope_ref=None,
        name="routing_strategy_code", default=0,
    )
    if forced_code is not None:
        code = int(float(forced_code))
        if code == 1:  # linear forcé
            min_savings = float("inf")
        elif code == 2:  # parallel forcé
            min_savings = -float("inf")
        # code == 0 (hybrid) : laisse min_savings au défaut

    of_row = conn.execute(
        "SELECT of_id, article_id, quantity FROM manufacturing_orders "
        "WHERE of_id = ?",
        (of_id,),
    ).fetchone()
    if of_row is None:
        return []
    article_id = of_row["article_id"]
    qty = float(of_row["quantity"])

    op_rows = conn.execute(
        """
        SELECT of_op_id, sequence_idx, workstation_id, unit_time_min
        FROM order_operations
        WHERE of_id = ? AND status = 'pending'
        ORDER BY sequence_idx ASC
        """,
        (of_id,),
    ).fetchall()
    if not op_rows:
        return []

    load_by_ws = _current_pending_load_min(conn)
    decisions: list[ArbitrageDecision] = []

    for op in op_rows:
        of_op_id = int(op["of_op_id"])
        seq = int(op["sequence_idx"])
        ws_orig = op["workstation_id"]
        ut_orig = float(op["unit_time_min"])
        candidates = _candidate_durations(
            conn, article_id, seq, ws_orig, ut_orig,
        )
        if len(candidates) == 1:
            # Pas d'alternative → linéaire pur
            duration_orig = (
                ut_orig * qty / workstation_capacity_factor(conn, ws_orig)
            )
            eft_orig = load_by_ws.get(ws_orig, 0.0) + duration_orig
            load_by_ws[ws_orig] = eft_orig
            decisions.append(ArbitrageDecision(
                of_op_id=of_op_id, sequence_idx=seq,
                original_workstation=ws_orig,
                chosen_workstation=ws_orig,
                original_eft=eft_orig,
                chosen_eft=eft_orig,
                savings_min=0.0,
                strategy="linear",
            ))
            continue

        # Évalue chaque candidat
        scored: list[tuple[str, float, float]] = []
        # (ws, duration_with_capa, eft)
        for ws, ut in candidates:
            capa = workstation_capacity_factor(conn, ws)
            duration = ut * qty / (capa if capa > 0 else 1.0)
            eft = load_by_ws.get(ws, 0.0) + duration
            scored.append((ws, ut, eft))
        # Choisit le min EFT
        scored.sort(key=lambda t: t[2])
        best_ws, best_ut, best_eft = scored[0]
        # Référence : poste préféré
        orig_capa = workstation_capacity_factor(conn, ws_orig)
        orig_dur = ut_orig * qty / (orig_capa if orig_capa > 0 else 1.0)
        orig_eft = load_by_ws.get(ws_orig, 0.0) + orig_dur

        savings = orig_eft - best_eft
        if best_ws != ws_orig and savings >= min_savings:
            # Bascule sur l'alternative
            conn.execute(
                "UPDATE order_operations SET workstation_id = ?, "
                "unit_time_min = ? WHERE of_op_id = ?",
                (best_ws, best_ut, of_op_id),
            )
            load_by_ws[best_ws] = best_eft
            decisions.append(ArbitrageDecision(
                of_op_id=of_op_id, sequence_idx=seq,
                original_workstation=ws_orig,
                chosen_workstation=best_ws,
                original_eft=orig_eft,
                chosen_eft=best_eft,
                savings_min=savings,
                strategy="parallel",
            ))
        else:
            # Reste sur le préféré
            load_by_ws[ws_orig] = orig_eft
            decisions.append(ArbitrageDecision(
                of_op_id=of_op_id, sequence_idx=seq,
                original_workstation=ws_orig,
                chosen_workstation=ws_orig,
                original_eft=orig_eft,
                chosen_eft=orig_eft,
                savings_min=0.0,
                strategy="linear",
            ))

    return decisions


def routing_strategy_of(decisions: list[ArbitrageDecision]) -> str:
    """Synthétise la stratégie d'un OF entier :
       - 'linear' si toutes les ops restent sur le préféré
       - 'parallel' si toutes basculent
       - 'hybrid' si mix
    """
    if not decisions:
        return "linear"
    switched = sum(1 for d in decisions if d.chosen_workstation != d.original_workstation)
    if switched == 0:
        return "linear"
    if switched == len(decisions):
        return "parallel"
    return "hybrid"
