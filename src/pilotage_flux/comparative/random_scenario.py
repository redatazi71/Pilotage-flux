"""Générateur de scénarios aléatoires (L10.2).

À partir d'un set de fixtures (généré par data_factory ou existant), produit
un `Scenario` paramétrable et reproductible (seed déterministe). Les aléas
sont tirés selon un mix configurable.

Usage typique :

    fixtures_dir = Path("data/runs/random_fix_42")
    generate_random_fixtures(FixtureSpec(), seed=42, out_dir=fixtures_dir)
    scenario = generate_random_scenario(
        RandomScenarioSpec(), seed=100, fixtures_dir=fixtures_dir,
    )
    run_doctrine(scenario, "event", db_path, fixtures_dir=fixtures_dir)
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from pilotage_flux.comparative.scenario import (
    HAZARD_BREAKDOWN,
    HAZARD_LOGISTIC_DELAY,
    HAZARD_PO_DELAY,
    HAZARD_QUALITY_NC,
    HAZARD_URGENT_ORDER,
    HazardEvent,
    Scenario,
)


@dataclass
class RandomScenarioSpec:
    """Spécification d'un scénario aléatoire."""

    name_prefix: str = "random"
    n_sales_orders: int = 8
    sales_order_qty_range: tuple[int, int] = (50, 200)
    horizon_days: int = 18
    horizon_start: str = "2026-07-06"
    n_hazards: int = 5
    hazard_kinds: list[str] = field(
        default_factory=lambda: [
            HAZARD_BREAKDOWN, HAZARD_QUALITY_NC,
            HAZARD_PO_DELAY, HAZARD_URGENT_ORDER,
        ]
    )
    # Probabilité (poids relatif) de chaque type d'aléa
    hazard_weights: dict[str, float] = field(
        default_factory=lambda: {
            HAZARD_BREAKDOWN: 0.30,
            HAZARD_QUALITY_NC: 0.30,
            HAZARD_PO_DELAY: 0.20,
            HAZARD_URGENT_ORDER: 0.20,
        }
    )
    breakdown_duration_range: tuple[int, int] = (2, 5)
    breakdown_factor_range: tuple[float, float] = (1.5, 3.0)
    nc_scrap_range: tuple[int, int] = (10, 25)
    po_delay_range: tuple[int, int] = (3, 10)
    urgent_qty_range: tuple[int, int] = (20, 60)
    logistic_block_range: tuple[int, int] = (2, 4)
    # Stocks initiaux : pour chaque composant, niveau aléatoire
    initial_stock_range: tuple[int, int] = (200, 1500)
    # Achats ouverts initiaux
    n_initial_pos: int = 3
    initial_po_qty_range: tuple[int, int] = (300, 1000)


def _read_articles(fixtures_dir: Path) -> dict[str, list[str]]:
    """Lit articles.csv et classe par type (finis / semis / composants)."""
    finished: list[str] = []
    semis: list[str] = []
    components: list[str] = []
    with (fixtures_dir / "articles.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            aid = row["article_id"]
            is_purchased = int(row.get("is_purchased") or 0) == 1
            if is_purchased:
                components.append(aid)
            elif aid.startswith("SEMI"):
                semis.append(aid)
            else:
                finished.append(aid)
    return {
        "finished": finished, "semis": semis, "components": components,
    }


def _read_workstations(fixtures_dir: Path) -> list[str]:
    with (fixtures_dir / "workstations.csv").open(encoding="utf-8") as f:
        return [row["workstation_id"] for row in csv.DictReader(f)]


def _pick_hazard_kind(rng: random.Random, spec: RandomScenarioSpec) -> str:
    """Tirage pondéré du type d'aléa."""
    kinds = [k for k in spec.hazard_kinds if spec.hazard_weights.get(k, 0) > 0]
    weights = [spec.hazard_weights.get(k, 0) for k in kinds]
    return rng.choices(kinds, weights=weights, k=1)[0]


def _build_hazard(
    rng: random.Random,
    kind: str,
    day: int,
    spec: RandomScenarioSpec,
    fixtures: dict[str, list[str]],
    workstations: list[str],
    po_ids_pool: list[str],
) -> HazardEvent | None:
    if kind == HAZARD_BREAKDOWN:
        ws = rng.choice(workstations)
        duration = rng.randint(*spec.breakdown_duration_range)
        factor = round(rng.uniform(*spec.breakdown_factor_range), 2)
        return HazardEvent(
            day=day, kind=HAZARD_BREAKDOWN,
            payload={
                "workstation_id": ws,
                "slowdown_factor": factor,
                "duration_days": duration,
            },
        )
    if kind == HAZARD_QUALITY_NC:
        # Tire un article (fini ou semi de préférence)
        targets = fixtures["finished"] + fixtures["semis"]
        if not targets:
            return None
        article = rng.choice(targets)
        qty_scrap = rng.randint(*spec.nc_scrap_range)
        severity = rng.choice(["normal", "high", "critical"])
        return HazardEvent(
            day=day, kind=HAZARD_QUALITY_NC,
            payload={
                "article_id": article,
                "qty_scrap": qty_scrap,
                "severity": severity,
            },
        )
    if kind == HAZARD_PO_DELAY:
        if not po_ids_pool:
            return None
        po = rng.choice(po_ids_pool)
        delay = rng.randint(*spec.po_delay_range)
        return HazardEvent(
            day=day, kind=HAZARD_PO_DELAY,
            payload={"po_id": po, "delay_days": delay},
        )
    if kind == HAZARD_URGENT_ORDER:
        if not fixtures["finished"]:
            return None
        article = rng.choice(fixtures["finished"])
        qty = rng.randint(*spec.urgent_qty_range)
        due_day = min(spec.horizon_days - 1, day + rng.randint(5, 10))
        return HazardEvent(
            day=day, kind=HAZARD_URGENT_ORDER,
            payload={
                "sales_order_id": f"SO-URG-RAND-{day}",
                "article_id": article,
                "quantity": qty,
                "due_day": due_day,
            },
        )
    if kind == HAZARD_LOGISTIC_DELAY:
        # §24.9 — Logistique : un poste bloqué (flux interne interrompu)
        ws = rng.choice(workstations)
        block_days = rng.randint(*spec.logistic_block_range)
        return HazardEvent(
            day=day, kind=HAZARD_LOGISTIC_DELAY,
            payload={
                "workstation_id": ws,
                "block_days": block_days,
            },
        )
    return None


def generate_random_scenario(
    spec: RandomScenarioSpec,
    seed: int,
    fixtures_dir: Path,
) -> Scenario:
    """Construit un Scenario aléatoire cohérent avec les fixtures `fixtures_dir`.

    Le `seed` contrôle :
      - le choix des articles dans les SO
      - les quantités
      - les types/dates d'aléas
      - le poste touché par chaque breakdown
    """
    rng = random.Random(seed)
    fixtures = _read_articles(fixtures_dir)
    workstations = _read_workstations(fixtures_dir)
    if not fixtures["finished"] or not workstations:
        raise ValueError(
            f"Fixtures incomplètes dans {fixtures_dir} "
            "(articles finis ou postes manquants)"
        )

    # Sales orders aléatoires
    sales_orders: list[dict] = []
    horizon_start_d = date.fromisoformat(spec.horizon_start)
    for i in range(spec.n_sales_orders):
        article = rng.choice(fixtures["finished"])
        qty = rng.randint(*spec.sales_order_qty_range)
        due_offset = rng.randint(8, max(8, spec.horizon_days - 3))
        due = (horizon_start_d + timedelta(days=due_offset)).strftime("%Y-%m-%d")
        sales_orders.append({
            "sales_order_id": f"SO-{i+1:03d}",
            "article_id": article,
            "quantity": qty,
            "due_date": due,
        })

    # Stocks initiaux pour chaque composant
    initial_stocks = {
        comp: float(rng.randint(*spec.initial_stock_range))
        for comp in fixtures["components"]
    }

    # POs initiaux
    initial_pos: list[dict] = []
    po_ids_pool: list[str] = []
    for i in range(spec.n_initial_pos):
        if not fixtures["components"]:
            break
        comp = rng.choice(fixtures["components"])
        qty = rng.randint(*spec.initial_po_qty_range)
        expected_day = rng.randint(2, max(2, spec.horizon_days // 2))
        po_id = f"PO-{i+1:04d}"
        initial_pos.append({
            "po_id": po_id,
            "article_id": comp,
            "qty": qty,
            "expected_day": expected_day,
        })
        po_ids_pool.append(po_id)

    # Aléas
    hazards: list[HazardEvent] = []
    # Tire des jours uniques (sans doublon) en évitant le 1er et le dernier
    possible_days = list(range(2, max(3, spec.horizon_days - 1)))
    n_hazards = min(spec.n_hazards, len(possible_days))
    hazard_days = sorted(rng.sample(possible_days, n_hazards))
    for day in hazard_days:
        kind = _pick_hazard_kind(rng, spec)
        h = _build_hazard(
            rng, kind, day, spec, fixtures, workstations, po_ids_pool,
        )
        if h is not None:
            hazards.append(h)

    return Scenario(
        name=f"{spec.name_prefix}_{seed}",
        seed=seed,
        horizon_days=spec.horizon_days,
        horizon_start=spec.horizon_start,
        initial_sales_orders=sales_orders,
        initial_stocks=initial_stocks,
        initial_purchase_orders=initial_pos,
        hazards=hazards,
    )
