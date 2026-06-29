"""Génération paramétrable de fixtures référentiels reproductibles (L10.1).

`generate_random_fixtures(spec, seed, out_dir)` écrit 7 CSVs dans `out_dir/`,
compatibles avec `import_referentials`. Tous les paramètres sont
paramétrables via `FixtureSpec` :

  - Tailles : n_finished_articles, n_semi_articles, n_components,
    n_workstations, daily_minutes.
  - Structure : max_ops_per_*, max_bom_children_per_*, depth de la BOM.
  - Distributions : unit_time_min_range, capacity_factor_range,
    hourly_rate_range, unit_cost_*_range.
  - Multi-goulots : `bottleneck_workstation_indices` reçoivent un
    capacity_factor réduit (~0.5), garantissant qu'ils saturent en charge.

Format de génération :
  - Articles : ART-A, ART-B, … (finis), SEMI-1, …, COMP-X, …
  - Workstations : WS-1, WS-2, …
"""

from __future__ import annotations

import csv
import random
import string
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FixtureSpec:
    """Spécification d'un set de fixtures aléatoire."""

    n_finished_articles: int = 6
    n_semi_articles: int = 6
    n_components: int = 8
    n_workstations: int = 8
    max_ops_per_finished: int = 5
    min_ops_per_finished: int = 3
    max_ops_per_semi: int = 3
    min_ops_per_semi: int = 1
    max_bom_children_per_finished: int = 4
    min_bom_children_per_finished: int = 2
    max_bom_children_per_semi: int = 3
    min_bom_children_per_semi: int = 1
    # finished article peut être composé de semis OU de composants ;
    # ratio_finished_uses_semis = probabilité qu'un enfant soit un semi (vs composant)
    ratio_finished_uses_semis: float = 0.6
    unit_time_min_range: tuple[float, float] = (0.5, 4.0)
    bom_quantity_range: tuple[int, int] = (1, 4)
    capacity_factor_range: tuple[float, float] = (0.70, 0.95)
    bottleneck_capacity_factor_range: tuple[float, float] = (0.40, 0.60)
    yield_rate_range: tuple[float, float] = (0.95, 0.99)
    hourly_rate_range: tuple[float, float] = (25.0, 60.0)
    unit_cost_finished_range: tuple[float, float] = (15.0, 40.0)
    unit_cost_semi_range: tuple[float, float] = (5.0, 12.0)
    unit_cost_component_range: tuple[float, float] = (0.5, 5.0)
    daily_minutes: int = 960
    n_initial_sales_orders: int = 6
    sales_order_quantity_range: tuple[int, int] = (50, 200)
    horizon_label_start: str = "2026-07-06"
    # Postes-goulots : indices 1-based (1..n_workstations) qui reçoivent
    # un capacity_factor réduit pour saturer la charge.
    bottleneck_workstation_indices: list[int] = field(
        default_factory=lambda: [3, 6]
    )
    # Moi & overhead par défaut (insérés en parameters)
    moi_overhead_rate: float = 0.30
    moi_fixed_per_of: float = 50.0


DEFAULT_SPEC = FixtureSpec()


def _alpha_index(i: int) -> str:
    """0 -> 'A', 1 -> 'B', …, 25 -> 'Z', 26 -> 'AA', etc."""
    letters = string.ascii_uppercase
    if i < 26:
        return letters[i]
    first = i // 26 - 1
    second = i % 26
    return letters[first] + letters[second]


def _u(rng: random.Random, lo: float, hi: float) -> float:
    return rng.uniform(lo, hi)


def _ri(rng: random.Random, lo: int, hi: int) -> int:
    return rng.randint(lo, hi)


def _write_csv(path: Path, headers: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for r in rows:
            w.writerow(r)


def generate_random_fixtures(
    spec: FixtureSpec, seed: int, out_dir: Path
) -> dict[str, list[str]]:
    """Génère les 7 CSVs de fixtures depuis spec + seed dans `out_dir/`.

    Renvoie un index {fichier: liste des IDs principaux} utile pour les
    contrôles d'intégrité et la génération de scénarios cohérents.
    """
    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- Articles
    finished_ids = [f"ART-{_alpha_index(i)}" for i in range(spec.n_finished_articles)]
    semi_ids = [f"SEMI-{i+1}" for i in range(spec.n_semi_articles)]
    component_ids = [
        f"COMP-{_alpha_index(i)}" for i in range(spec.n_components)
    ]
    articles_rows = []
    for aid in finished_ids:
        articles_rows.append([aid, f"Produit fini {aid}", "PCE", 0])
    for aid in semi_ids:
        articles_rows.append([aid, f"Semi-fini {aid}", "PCE", 0])
    for aid in component_ids:
        articles_rows.append([aid, f"Composant acheté {aid}", "PCE", 1])
    _write_csv(
        out_dir / "articles.csv",
        ["article_id", "label", "unit", "is_purchased"],
        articles_rows,
    )

    # --- Workstations
    ws_ids = [f"WS-{i+1}" for i in range(spec.n_workstations)]
    _write_csv(
        out_dir / "workstations.csv",
        ["workstation_id", "label", "sequence_idx"],
        [
            [wid, f"Poste {wid}", i + 1]
            for i, wid in enumerate(ws_ids)
        ],
    )

    # --- Calendar
    _write_csv(
        out_dir / "calendars.csv",
        ["calendar_id", "label", "daily_minutes", "working_days"],
        [
            ["CAL-DEFAULT", f"Calendrier ({spec.daily_minutes} min/j)",
             spec.daily_minutes, "mon,tue,wed,thu,fri"],
        ],
    )

    # --- BOM lines : finished -> mix(semi, component), semi -> component
    bom_rows: list[list] = []
    for fin in finished_ids:
        n_children = _ri(
            rng, spec.min_bom_children_per_finished,
            spec.max_bom_children_per_finished,
        )
        # Force au moins 1 semi pour les multi-niveaux
        children_pool: list[str] = []
        for _ in range(n_children):
            if rng.random() < spec.ratio_finished_uses_semis and semi_ids:
                children_pool.append(rng.choice(semi_ids))
            else:
                children_pool.append(rng.choice(component_ids))
        # Dédoublonne (un parent peut référencer un enfant 1 fois)
        seen: set[str] = set()
        for child in children_pool:
            if child in seen:
                continue
            seen.add(child)
            qty = _ri(rng, *spec.bom_quantity_range)
            bom_rows.append([fin, child, qty])
    for semi in semi_ids:
        n_children = _ri(
            rng, spec.min_bom_children_per_semi,
            spec.max_bom_children_per_semi,
        )
        seen = set()
        for _ in range(n_children):
            child = rng.choice(component_ids)
            if child in seen:
                continue
            seen.add(child)
            qty = _ri(rng, *spec.bom_quantity_range)
            bom_rows.append([semi, child, qty])
    _write_csv(
        out_dir / "bom_lines.csv",
        ["parent_article", "child_article", "quantity"],
        bom_rows,
    )

    # --- Routings : finished -> ops sur N postes ; semi -> 1-2 ops
    routing_rows: list[list] = []
    for fin in finished_ids:
        n_ops = _ri(
            rng, spec.min_ops_per_finished, spec.max_ops_per_finished,
        )
        # Sélectionne n_ops postes distincts (sample sans remise)
        n_ops = min(n_ops, len(ws_ids))
        chosen_ws = rng.sample(ws_ids, n_ops)
        for seq_idx, wid in enumerate(chosen_ws, start=1):
            unit_time = round(_u(rng, *spec.unit_time_min_range), 2)
            routing_rows.append([fin, seq_idx, wid, unit_time])
    for semi in semi_ids:
        n_ops = _ri(rng, spec.min_ops_per_semi, spec.max_ops_per_semi)
        n_ops = min(n_ops, len(ws_ids))
        chosen_ws = rng.sample(ws_ids, n_ops)
        for seq_idx, wid in enumerate(chosen_ws, start=1):
            unit_time = round(_u(rng, *spec.unit_time_min_range), 2)
            routing_rows.append([semi, seq_idx, wid, unit_time])
    _write_csv(
        out_dir / "routing_operations.csv",
        ["article_id", "sequence_idx", "workstation_id", "unit_time_min"],
        routing_rows,
    )

    # --- Parameters : capacités + rendements + coûts + moi
    param_rows: list[list] = []
    param_rows.append(["global", "", "planning_horizon_days", 30, ""])
    param_rows.append(["global", "", "replan_interval_days", 7, ""])
    param_rows.append(["global", "", "p2_capacity_risk_ratio", 1.0, ""])
    param_rows.append(["global", "", "p2_capacity_block_ratio", 1.5, ""])
    param_rows.append(["global", "", "risk_debt_default_deadline_days", 7, ""])
    param_rows.append(
        ["global", "", "moi_overhead_rate", spec.moi_overhead_rate, ""]
    )
    param_rows.append(
        ["global", "", "moi_fixed_per_of", spec.moi_fixed_per_of, ""]
    )
    bottleneck_set = {
        f"WS-{idx}" for idx in spec.bottleneck_workstation_indices
        if 1 <= idx <= spec.n_workstations
    }
    for wid in ws_ids:
        if wid in bottleneck_set:
            cap = round(_u(rng, *spec.bottleneck_capacity_factor_range), 2)
        else:
            cap = round(_u(rng, *spec.capacity_factor_range), 2)
        yld = round(_u(rng, *spec.yield_rate_range), 2)
        rate = round(_u(rng, *spec.hourly_rate_range), 1)
        param_rows.append(["workstation", wid, "capacity_factor", cap, ""])
        param_rows.append(["workstation", wid, "yield_rate", yld, ""])
        param_rows.append(["workstation", wid, "hourly_rate", rate, ""])
    for aid in finished_ids:
        cost = round(_u(rng, *spec.unit_cost_finished_range), 2)
        param_rows.append(["article", aid, "unit_cost", cost, ""])
    for aid in semi_ids:
        cost = round(_u(rng, *spec.unit_cost_semi_range), 2)
        param_rows.append(["article", aid, "unit_cost", cost, ""])
    for aid in component_ids:
        cost = round(_u(rng, *spec.unit_cost_component_range), 2)
        param_rows.append(["article", aid, "unit_cost", cost, ""])
    _write_csv(
        out_dir / "parameters.csv",
        ["scope", "scope_ref", "name", "value_num", "value_text"],
        param_rows,
    )

    # --- Sales orders : N commandes initiales sur des finis aléatoires
    so_rows: list[list] = []
    so_due_base = spec.horizon_label_start
    for i in range(spec.n_initial_sales_orders):
        fin = rng.choice(finished_ids)
        qty = _ri(rng, *spec.sales_order_quantity_range)
        # Due date : horizon + 9..20 jours
        from datetime import date, timedelta
        due_offset = _ri(rng, 9, 20)
        due = (date.fromisoformat(so_due_base)
               + timedelta(days=due_offset)).strftime("%Y-%m-%d")
        so_rows.append([f"SO-{i+1:03d}", fin, qty, due])
    _write_csv(
        out_dir / "sales_orders.csv",
        ["sales_order_id", "article_id", "quantity", "due_date"],
        so_rows,
    )

    return {
        "finished_articles": finished_ids,
        "semi_articles": semi_ids,
        "components": component_ids,
        "workstations": ws_ids,
        "bottleneck_workstations": sorted(bottleneck_set),
        "sales_orders": [r[0] for r in so_rows],
    }
