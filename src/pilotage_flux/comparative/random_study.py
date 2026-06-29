"""Étude comparative sur fixtures + scénarios aléatoires (L10.4).

Combine `data_factory.generate_random_fixtures` et
`comparative.random_scenario.generate_random_scenario` pour produire des
études stochastiques : N fixture sets × M scénarios × 4 doctrines.

Chaque combinaison (fixture_seed, scenario_seed) génère un environnement
industriel et un scénario d'aléas indépendants — la variance mesure la
robustesse des doctrines à la variabilité des configurations, pas
seulement à la variabilité des aléas (qu'on avait avec jitter_scenario).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pilotage_flux.comparative.kpis import KpiSet, compute_kpis
from pilotage_flux.comparative.random_scenario import (
    RandomScenarioSpec,
    generate_random_scenario,
)
from pilotage_flux.comparative.runner import run_doctrine
from pilotage_flux.comparative.scenario import DOCTRINES
from pilotage_flux.comparative.variance import AggregatedKpi, aggregate_kpis
from pilotage_flux.data_factory import (
    DEFAULT_SPEC,
    FixtureSpec,
    generate_random_fixtures,
)


@dataclass
class RandomStudy:
    fixture_seeds: list[int]
    scenario_seeds: list[int]
    aggregates: dict[str, AggregatedKpi] = field(default_factory=dict)
    # aggregates[doctrine] = AggregatedKpi sur l'ensemble du grid


def run_random_study(
    *,
    fixture_seeds: list[int],
    scenario_seeds: list[int],
    work_dir: Path,
    fixture_spec: FixtureSpec | None = None,
    scenario_spec: RandomScenarioSpec | None = None,
    doctrines: list[str] | None = None,
) -> RandomStudy:
    """Exécute pour chaque (fixture_seed, scenario_seed, doctrine) un run.

    Total = len(fixture_seeds) × len(scenario_seeds) × len(doctrines).
    Les KPIs sont ensuite agrégés par doctrine sur l'ensemble du grid.
    """
    if fixture_spec is None:
        fixture_spec = DEFAULT_SPEC
    if scenario_spec is None:
        scenario_spec = RandomScenarioSpec()
    if doctrines is None:
        doctrines = list(DOCTRINES)

    work_dir.mkdir(parents=True, exist_ok=True)
    kpis_per_doctrine: dict[str, list[KpiSet]] = {d: [] for d in doctrines}

    for fix_seed in fixture_seeds:
        fix_dir = work_dir / f"fixtures_{fix_seed}"
        generate_random_fixtures(fixture_spec, seed=fix_seed, out_dir=fix_dir)
        for scen_seed in scenario_seeds:
            scen = generate_random_scenario(
                scenario_spec, seed=scen_seed, fixtures_dir=fix_dir,
            )
            for d in doctrines:
                db_path = (
                    work_dir / f"run_{fix_seed}_{scen_seed}_{d}.db"
                )
                result = run_doctrine(
                    scen, d, db_path, fixtures_dir=fix_dir,
                )
                kpis_per_doctrine[d].append(compute_kpis(scen, result))

    study = RandomStudy(
        fixture_seeds=list(fixture_seeds),
        scenario_seeds=list(scenario_seeds),
    )
    for d in doctrines:
        study.aggregates[d] = aggregate_kpis(d, "random_grid", kpis_per_doctrine[d])
    return study


def build_random_study_report(study: RandomStudy) -> str:
    """Rapport Markdown : 1 ligne par doctrine, décomposition vs OF."""
    lines: list[str] = []
    total = (
        len(study.fixture_seeds) * len(study.scenario_seeds)
        * len(study.aggregates)
    )
    lines.append("# Étude comparative aléatoire (L10)")
    lines.append("")
    lines.append(
        f"**{len(study.fixture_seeds)} fixture sets** × "
        f"**{len(study.scenario_seeds)} scénarios** × "
        f"**{len(study.aggregates)} doctrines** = {total} runs"
    )
    lines.append("")
    lines.append(f"Seeds fixtures : `{study.fixture_seeds}`")
    lines.append(f"Seeds scénarios : `{study.scenario_seeds}`")
    lines.append("")
    lines.append("## Résultats agrégés par doctrine")
    lines.append("")
    lines.append("| Doctrine | Lead time | WIP | Coût total | Recalc APS | Nervosité | Détections | Causes |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for d, a in study.aggregates.items():
        lines.append(
            f"| {d.upper()} "
            f"| {a.lead_time_avg_mean:.2f} ± {a.lead_time_avg_std:.2f} "
            f"| {a.wip_mean:.2f} ± {a.wip_std:.2f} "
            f"| {a.total_cost_eur_mean:.0f} ± {a.total_cost_eur_std:.0f} € "
            f"| {a.aps_recalculations_mean:.1f} "
            f"| {a.nervousness_mean:.3f} "
            f"| {a.deviations_detected_mean:.1f} "
            f"| {a.causes_attached_mean:.1f} |"
        )
    lines.append("")

    # Décomposition 2×2 vs OF
    if "of" in study.aggregates:
        of_cost = study.aggregates["of"].total_cost_eur_mean
        lines.append("## Décomposition 2×2 — Δ coût (€) vs OF")
        lines.append("")
        lines.append("| | Flux ✗ | Flux ✓ |")
        lines.append("|---|---|---|")
        of_cost_str = "0 (réf)"
        flux_delta = "—"
        if "flux" in study.aggregates:
            flux_delta = f"{study.aggregates['flux'].total_cost_eur_mean - of_cost:+.0f}"
        lines.append(f"| **Event ✗** | {of_cost_str} | {flux_delta} |")
        ofe_delta = "—"
        ev_delta = "—"
        if "of_event" in study.aggregates:
            ofe_delta = f"{study.aggregates['of_event'].total_cost_eur_mean - of_cost:+.0f}"
        if "event" in study.aggregates:
            ev_delta = f"{study.aggregates['event'].total_cost_eur_mean - of_cost:+.0f}"
        lines.append(f"| **Event ✓** | {ofe_delta} | **{ev_delta}** |")
        lines.append("")
        lines.append(
            "Lecture : Δ négatif = économie vs OF. Coût OF = "
            f"{of_cost:.0f} € sur l'ensemble du grid."
        )
        lines.append("")
    return "\n".join(lines)
