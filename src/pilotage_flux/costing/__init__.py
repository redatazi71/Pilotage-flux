"""Modèle de coûts (L7.1 / cadrage §180.c).

Trois composantes :
  - **Matière** : Σ (composants achetés × prix unitaire × quantité OF)
  - **MOD** (main d'œuvre directe) : Σ (temps réel d'op × taux horaire poste)
  - **MOI** (main d'œuvre indirecte / overhead) : ratio × MOD + montant fixe par OF

Tous les coûts sont **data-driven** via la table `parameters` :
  - `unit_cost`      (scope='article', scope_ref=article_id)
  - `hourly_rate`    (scope='workstation', scope_ref=ws_id) — €/h
  - `moi_overhead_rate` (scope='global')
  - `moi_fixed_per_of`  (scope='global')

Aucun prix codé en dur. Si un paramètre est absent, on retombe sur 0
(silent fallback explicite : on signale dans le breakdown les composants
non valorisés via `unvalued_articles`).
"""

from pilotage_flux.costing.engine import (
    DEFAULT_MOI_FIXED_PER_OF,
    DEFAULT_MOI_OVERHEAD_RATE,
    OFCostBreakdown,
    RunCostReport,
    compute_of_cost,
    compute_run_cost_report,
    seed_default_unit_costs,
)


__all__ = [
    "DEFAULT_MOI_FIXED_PER_OF",
    "DEFAULT_MOI_OVERHEAD_RATE",
    "OFCostBreakdown",
    "RunCostReport",
    "compute_of_cost",
    "compute_run_cost_report",
    "seed_default_unit_costs",
]
