"""§24.10.4 — Étude par zone décisionnelle × stress cascadé.

Différence avec build_zone_study.py :
- 5 hazards cascadés (au lieu d'1 isolé) sur une fenêtre courte
  autour du jour cible de la zone
- Objectif : discriminer V13.D (capa-aware) et V13.E (TOC-aware).
  Le buffer TOC de V13.E devrait absorber les chocs répétés en
  amont du goulot mieux que la simple placement capa-aware.

Grille : 5 domaines × 3 zones × 4 doctrines × (3 modes FLUX/EVENT
ou 1 mode OF/OF+EVENT) × 5 seeds = ~600 runs.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Réutilise l'infra du zone_study de base
import build_zone_study as base

from pilotage_flux.comparative.random_scenario import (
    RandomScenarioSpec, generate_random_scenario,
)
from pilotage_flux.comparative.resilience import (
    DOMAIN_TO_HAZARD, _build_hazard,
)
from pilotage_flux.comparative.scenario import Scenario


def _build_cascaded_zone_scenario(
    base_seed: int, domain: str, zone: str, fixtures_dir,
) -> Scenario:
    """5 hazards du même domaine, cascadés autour du jour de la zone.

    Cible day = ZONE_TO_DAY[zone] ; hazards à day, day+1, day+2, day+3,
    day+4 (5 hazards consécutifs).
    """
    import random as _r
    rng = _r.Random(base_seed)
    spec = RandomScenarioSpec(
        n_hazards=0, n_sales_orders=20, horizon_days=base.HORIZON_DAYS,
    )
    scen = generate_random_scenario(spec, seed=base_seed,
                                     fixtures_dir=fixtures_dir)
    base_day = base.ZONE_TO_DAY[zone]
    hazards = []
    for i in range(5):
        h = _build_hazard(
            rng, DOMAIN_TO_HAZARD[domain],
            day=min(base_day + i, base.HORIZON_DAYS - 2),
            fixtures_dir=fixtures_dir,
        )
        if h is not None:
            hazards.append(h)
    return Scenario(
        name=f"cascade_{domain}_{zone}_seed{base_seed}",
        seed=base_seed,
        horizon_days=base.HORIZON_DAYS,
        horizon_start=scen.horizon_start,
        initial_sales_orders=scen.initial_sales_orders,
        initial_stocks=scen.initial_stocks,
        initial_purchase_orders=scen.initial_purchase_orders,
        hazards=hazards,
    )


# Override le builder utilisé par le zone_study
base._build_zone_scenario = _build_cascaded_zone_scenario

# Override les paths de sortie
base.CHARTS_DIR = base.HERE / "charts"
base.DATA_MD = base.HERE / "cadrage_v4_zone_study_cascaded_data.md"


def main() -> int:
    print("=== §24.10.4 — Zone × cascade (5 hazards) ===")
    print(f"Chaque cellule : 5 hazards consécutifs sur "
          f"[day, day+4] où day = ZONE_TO_DAY[zone]")
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
