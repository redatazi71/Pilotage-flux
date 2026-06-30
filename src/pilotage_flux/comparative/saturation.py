"""Calibration de la saturation R1 par volume de SOs (Goldilocks #1).

Le cadrage v1.3 (section 9.1) fixe la zone Goldilocks à 85–90 % de
saturation R1. Pour balayer la robustesse autour de cette plage, le
plan d'expérience traverse 5 niveaux : {0.78, 0.82, 0.86, 0.90, 0.94}.

Méthode (validée doctrinalement) : **on ne touche pas aux durées
d'opération ni au mix produit** (ces paramètres sont fixés par les
fixtures industrielles). On ajuste **le volume total de SOs** du
scénario pour atteindre la saturation cible — c'est la réalité
industrielle (la demande charge le goulot, pas les paramètres
machines).

Définitions :
  - load_R1 = Σ_OF (qty_OF × Σ_op_sur_R1 unit_time / capacity_factor_R1)
  - capacity_R1 = horizon_days × shift_minutes × capa_factor_R1
  - saturation_R1 = load_R1 / capacity_R1

L'identification du poste goulot R1 est faite ex-ante : c'est le WS
ayant la plus forte charge totale dans le scénario de référence (à
volume nominal). Une fois R1 fixé, le volume de SOs est scalé pour
atteindre la saturation cible.

API :
  - identify_bottleneck(conn, scenario) -> ws_id, load_per_ws
  - compute_saturation(conn, scenario, ws_id, shift_minutes=480) -> float
  - calibrate_scenario_to_saturation(scenario, target, *, ws_id, ...) -> Scenario

Le calibrage est déterministe (pas de runtime simulation requise) :
il calcule la charge théorique R1 à partir des routings + BOM +
qty_SO, puis applique un scaling uniforme sur initial_sales_orders.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from pilotage_flux.comparative.scenario import Scenario


SATURATION_TARGETS: tuple[float, ...] = (
    0.78, 0.82, 0.86, 0.90, 0.94, 1.00,
)
# 6 niveaux : balaye la zone Goldilocks (78-94%) puis sur-saturation
# (100%) qui sert de test de cohérence interne — la doctrine prédit
# que l'avantage cybernétique s'érode en sur-saturation.
ROUTING_STRATEGIES: tuple[str, ...] = ("linear", "parallel", "hybrid")
# Mapping nom → code numérique pour param_overrides (qui n'accepte que
# value_num) ; lu par aps/routing_arbitrage.py via routing_strategy_code.
ROUTING_STRATEGY_CODE: dict[str, int] = {
    "hybrid": 0,    # défaut (savings >= min_savings)
    "linear": 1,    # min_savings = +inf → jamais d'alternative
    "parallel": 2,  # min_savings = -inf → bascule dès qu'alternative existe
}
DEFAULT_SHIFT_MINUTES = 480  # 1 shift / jour ouvré, doctrine BCE
DEFAULT_BOTTLENECK_THRESHOLD = 0.001  # ignore WS quasi-vides


def _compute_load_per_ws(conn: sqlite3.Connection) -> dict[str, float]:
    """Calcule la charge théorique totale en minutes pour chaque WS,
    en cumulant les SOs présents en base × BOM × routing.

    Pour chaque SO :
      - quantité finale demandée → quantité au niveau article (BOM expansion)
      - pour chaque opération du routing, charge += qty × unit_time

    Renvoie {workstation_id : load_minutes}.
    """
    # 1. Expansion BOM : pour chaque SO, calculer la demande induite par article
    so_rows = conn.execute(
        "SELECT sales_order_id, article_id, quantity FROM sales_orders"
    ).fetchall()
    # Cache de l'expansion BOM (article fini → {article: qty_unitaire})
    bom_cache: dict[str, dict[str, float]] = {}

    def _expand_bom(root: str) -> dict[str, float]:
        if root in bom_cache:
            return bom_cache[root]
        # Récursif simple ; le BOM des fixtures actuelles est à profondeur 2
        result: dict[str, float] = {root: 1.0}
        children = conn.execute(
            "SELECT child_article, quantity FROM bom_lines WHERE parent_article = ?",
            (root,),
        ).fetchall()
        for c in children:
            sub = _expand_bom(c["child_article"])
            for art, qty in sub.items():
                result[art] = result.get(art, 0.0) + qty * float(c["quantity"])
        bom_cache[root] = result
        return result

    demand_per_article: dict[str, float] = {}
    for so in so_rows:
        expanded = _expand_bom(so["article_id"])
        for art, qty_per_unit in expanded.items():
            demand_per_article[art] = (
                demand_per_article.get(art, 0.0)
                + qty_per_unit * float(so["quantity"])
            )

    # 2. Charge par WS = Σ_article (demande × Σ_op_sur_ce_ws unit_time / capa)
    load_per_ws: dict[str, float] = {}
    for article, qty_total in demand_per_article.items():
        ops = conn.execute(
            "SELECT workstation_id, unit_time_min FROM routing_operations "
            "WHERE article_id = ?",
            (article,),
        ).fetchall()
        for op in ops:
            ws = op["workstation_id"]
            unit_time = float(op["unit_time_min"]) or 0.0
            # capacity_factor par WS via parameters
            capa_row = conn.execute(
                "SELECT value_num FROM parameters "
                "WHERE scope='workstation' AND scope_ref=? "
                "AND name='capacity_factor' "
                "AND (valid_to IS NULL OR valid_to > datetime('now'))",
                (ws,),
            ).fetchone()
            capa = float(capa_row["value_num"]) if capa_row else 1.0
            if capa <= 0:
                capa = 1.0
            load_per_ws[ws] = (
                load_per_ws.get(ws, 0.0) + qty_total * unit_time / capa
            )
    return load_per_ws


def identify_bottleneck(
    conn: sqlite3.Connection,
) -> tuple[str | None, dict[str, float]]:
    """Identifie le WS goulot = WS ayant la plus forte charge.

    Renvoie (ws_id_goulot, dict load_per_ws). ws_id peut être None
    si aucune charge n'est mesurable (base vide).
    """
    loads = _compute_load_per_ws(conn)
    if not loads:
        return None, {}
    significant = {
        ws: v for ws, v in loads.items() if v > DEFAULT_BOTTLENECK_THRESHOLD
    }
    if not significant:
        return None, loads
    ws_top = max(significant, key=significant.get)
    return ws_top, loads


def compute_saturation(
    conn: sqlite3.Connection,
    horizon_days: int,
    ws_id: str | None = None,
    shift_minutes: int = DEFAULT_SHIFT_MINUTES,
) -> float:
    """Saturation = load(ws) / (horizon_days × shift_minutes).

    Si `ws_id` est None, utilise le goulot identifié automatiquement.
    Renvoie 0.0 si pas de charge.
    """
    if ws_id is None:
        ws_id, _ = identify_bottleneck(conn)
        if ws_id is None:
            return 0.0
    loads = _compute_load_per_ws(conn)
    load = loads.get(ws_id, 0.0)
    capacity = max(1.0, float(horizon_days) * float(shift_minutes))
    return load / capacity


def _compute_saturation_for_scenario(
    scenario: Scenario,
    fixtures_dir: Path,
    ws_id: str | None = None,
    shift_minutes: int = DEFAULT_SHIFT_MINUTES,
) -> tuple[float, str | None]:
    """Calcule la saturation R1 sans lancer la simulation.

    Bootstrap minimal : schema + fixtures + SOs du scenario, puis
    compute_saturation. Renvoie (saturation, ws_goulot).
    """
    # Import différé pour éviter la dépendance circulaire avec runner
    from pilotage_flux.comparative.runner import _import_sales_orders
    from pilotage_flux.db import db_session, init_schema
    from pilotage_flux.importers.csv_importer import import_referentials

    with TemporaryDirectory(prefix="sat_calc_") as tmp:
        db_path = Path(tmp) / "sat.db"
        init_schema(db_path, drop_existing=True)
        with db_session(db_path) as conn:
            import_referentials(conn, fixtures_dir)
            _import_sales_orders(conn, scenario)
            if ws_id is None:
                ws_id, _ = identify_bottleneck(conn)
            sat = compute_saturation(
                conn, scenario.horizon_days, ws_id, shift_minutes,
            )
    return sat, ws_id


def calibrate_scenario_to_saturation(
    scenario: Scenario,
    target_saturation: float,
    *,
    fixtures_dir: Path,
    ws_id: str | None = None,
    shift_minutes: int = DEFAULT_SHIFT_MINUTES,
) -> Scenario:
    """Renvoie un scénario dont la saturation R1 vaut `target_saturation`.

    Méthode : calcule la saturation actuelle, applique un scaling
    uniforme `target / current` sur les `quantity` des
    `initial_sales_orders`. Mix produit et durées d'op inchangés.

    Si la saturation actuelle est 0 (pas de SOs), renvoie le scénario
    inchangé (rien à scaler). Idempotent à un epsilon de
    discrétisation près (les quantités sont arrondies au plus proche
    entier).
    """
    current, ws_found = _compute_saturation_for_scenario(
        scenario, fixtures_dir, ws_id, shift_minutes,
    )
    if current <= 0 or not scenario.initial_sales_orders:
        return scenario
    scale = target_saturation / current
    new_sos: list[dict] = []
    for so in scenario.initial_sales_orders:
        so2 = dict(so)
        old_qty = float(so2.get("quantity", 0) or 0)
        new_qty = max(1, int(round(old_qty * scale)))
        so2["quantity"] = new_qty
        new_sos.append(so2)
    return replace(scenario, initial_sales_orders=new_sos)
