"""Étude comparative étendue : variance multi-seeds + scénarios stress (L5.1).

Le scénario `baseline` ne fournit qu'un point de mesure. Pour évaluer la
robustesse doctrinale, on rejoue chaque scénario avec plusieurs seeds
(via jitter déterministe sur les paramètres des aléas) et on agrège
les KPIs.

Sortie : `VarianceStudy` = {scenario_name: {doctrine: AggregatedKpi}}.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pilotage_flux.comparative.kpis import KpiSet, compute_kpis
from pilotage_flux.comparative.runner import (
    DEFAULT_FIXTURES_DIR,
    run_doctrine,
)
from pilotage_flux.comparative.scenario import (
    ALL_SCENARIOS,
    ALL_SCENARIOS_ANY,
    DOCTRINES,
    Scenario,
    jitter_scenario,
)


@dataclass
class AggregatedKpi:
    """Statistiques sur N runs d'un (scenario, doctrine)."""

    doctrine: str
    scenario_name: str
    n_runs: int
    lead_time_avg_mean: float
    lead_time_avg_std: float
    lead_time_max_mean: float
    wip_mean: float
    wip_std: float
    aps_recalculations_mean: float
    nervousness_mean: float
    deviations_detected_mean: float
    actions_triggered_mean: float
    replan_global_mean: float
    causes_attached_mean: float
    quality_events_mean: float
    of_closed_mean: float
    total_cost_eur_mean: float = 0.0
    total_cost_eur_std: float = 0.0
    cost_per_of_eur_mean: float = 0.0
    cost_scrap_eur_mean: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _stat(xs: list[float]) -> tuple[float, float]:
    if not xs:
        return 0.0, 0.0
    if len(xs) == 1:
        return float(xs[0]), 0.0
    return float(statistics.mean(xs)), float(statistics.stdev(xs))


def aggregate_kpis(
    doctrine: str, scenario_name: str, kpis: list[KpiSet]
) -> AggregatedKpi:
    lt_avg_m, lt_avg_s = _stat([k.lead_time_days_avg for k in kpis])
    lt_max_m, _ = _stat([float(k.lead_time_days_max) for k in kpis])
    wip_m, wip_s = _stat([k.wip_avg for k in kpis])
    aps_m, _ = _stat([float(k.aps_recalculations) for k in kpis])
    nrv_m, _ = _stat([k.nervousness for k in kpis])
    dev_m, _ = _stat([float(k.deviations_detected) for k in kpis])
    act_m, _ = _stat([float(k.actions_triggered) for k in kpis])
    glb_m, _ = _stat([float(k.replan_global_actions) for k in kpis])
    cau_m, _ = _stat([float(k.causes_attached) for k in kpis])
    qua_m, _ = _stat([float(k.quality_events) for k in kpis])
    cls_m, _ = _stat([float(k.of_closed) for k in kpis])
    cost_m, cost_s = _stat([k.total_cost_eur for k in kpis])
    cost_per_m, _ = _stat([k.cost_per_of_eur for k in kpis])
    scrap_m, _ = _stat([k.cost_scrap_eur for k in kpis])
    return AggregatedKpi(
        doctrine=doctrine,
        scenario_name=scenario_name,
        n_runs=len(kpis),
        lead_time_avg_mean=round(lt_avg_m, 3),
        lead_time_avg_std=round(lt_avg_s, 3),
        lead_time_max_mean=round(lt_max_m, 3),
        wip_mean=round(wip_m, 3),
        wip_std=round(wip_s, 3),
        aps_recalculations_mean=round(aps_m, 2),
        nervousness_mean=round(nrv_m, 3),
        deviations_detected_mean=round(dev_m, 2),
        actions_triggered_mean=round(act_m, 2),
        replan_global_mean=round(glb_m, 2),
        causes_attached_mean=round(cau_m, 2),
        quality_events_mean=round(qua_m, 2),
        of_closed_mean=round(cls_m, 2),
        total_cost_eur_mean=round(cost_m, 2),
        total_cost_eur_std=round(cost_s, 2),
        cost_per_of_eur_mean=round(cost_per_m, 2),
        cost_scrap_eur_mean=round(scrap_m, 2),
    )


@dataclass
class VarianceStudy:
    seeds: list[int]
    scenarios: list[str]
    doctrines: list[str]
    aggregates: dict[str, dict[str, AggregatedKpi]] = field(default_factory=dict)
    # aggregates[scenario_name][doctrine] = AggregatedKpi


def run_variance_study(
    scenarios: list[str],
    doctrines: list[str],
    seeds: list[int],
    *,
    work_dir: Path,
    fixtures_dir: Path = DEFAULT_FIXTURES_DIR,
    on_run_complete: callable = None,
) -> VarianceStudy:
    """Exécute chaque (scenario, doctrine, seed) et agrège les KPIs.

    Le nombre total de runs = len(scenarios) × len(doctrines) × len(seeds).
    Chaque run écrit une DB dans `work_dir/{scenario}_{doctrine}_{seed}.db`.

    `on_run_complete(scenario_name, doctrine, seed_idx_total)` est appelé
    après chaque run pour permettre une barre de progression.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    study = VarianceStudy(seeds=list(seeds), scenarios=list(scenarios),
                          doctrines=list(doctrines))
    for scen_name in scenarios:
        if scen_name not in ALL_SCENARIOS_ANY:
            raise ValueError(f"Scénario inconnu : {scen_name}")
        base = ALL_SCENARIOS_ANY[scen_name]()
        study.aggregates.setdefault(scen_name, {})
        for d in doctrines:
            kpis_per_seed: list[KpiSet] = []
            for s in seeds:
                jittered = jitter_scenario(base, seed=s)
                db_path = work_dir / f"{scen_name}_{d}_{s}.db"
                result = run_doctrine(
                    jittered, d, db_path, fixtures_dir=fixtures_dir
                )
                kpis_per_seed.append(compute_kpis(jittered, result))
                if on_run_complete is not None:
                    on_run_complete(scen_name, d, s)
            study.aggregates[scen_name][d] = aggregate_kpis(
                d, scen_name, kpis_per_seed
            )
    return study


def build_variance_report(study: VarianceStudy) -> str:
    """Rapport Markdown agrégé : table par scénario + résumé global."""
    from pilotage_flux.comparative.scenario import (
        DOCTRINE_EVENT, DOCTRINE_FLUX, DOCTRINE_OF, DOCTRINE_OF_EVENT,
    )

    lines: list[str] = []
    lines.append("# Étude comparative étendue V5 — variance multi-seeds")
    lines.append("")
    lines.append(
        f"**{len(study.scenarios)} scénarios** × **{len(study.doctrines)} doctrines** "
        f"× **{len(study.seeds)} seeds** = {len(study.scenarios) * len(study.doctrines) * len(study.seeds)} runs."
    )
    lines.append("")
    lines.append(f"Seeds utilisées : `{study.seeds}`")
    lines.append("")
    lines.append("Chaque scénario est rejoué avec un bruit déterministe (timing ±1 jour, "
                 "magnitude ±20%) sur les aléas pour mesurer la stabilité doctrinale.")
    lines.append("")

    label = {
        DOCTRINE_OF: "OF",
        DOCTRINE_FLUX: "FLUX",
        DOCTRINE_OF_EVENT: "OF+EVENT",
        DOCTRINE_EVENT: "EVENT",
    }

    for scen in study.scenarios:
        lines.append(f"## Scénario `{scen}`")
        lines.append("")
        agg = study.aggregates.get(scen, {})
        if not agg:
            lines.append("_(aucun résultat)_")
            lines.append("")
            continue
        ordered = [agg[d] for d in study.doctrines if d in agg]
        header = (
            "| KPI | "
            + " | ".join(label.get(a.doctrine, a.doctrine) for a in ordered)
            + " |"
        )
        sep = "|---|" + "---|" * len(ordered)
        lines.append(header)
        lines.append(sep)
        rows = [
            ("Lead time moyen (j)", lambda a: f"{a.lead_time_avg_mean:.2f} ± {a.lead_time_avg_std:.2f}"),
            ("Lead time max (j)", lambda a: f"{a.lead_time_max_mean:.1f}"),
            ("WIP moyen", lambda a: f"{a.wip_mean:.2f} ± {a.wip_std:.2f}"),
            ("OF clôturés (moy.)", lambda a: f"{a.of_closed_mean:.1f}"),
            ("Recalculs APS (moy.)", lambda a: f"{a.aps_recalculations_mean:.1f}"),
            ("Nervosité (replan/jour)", lambda a: f"{a.nervousness_mean:.3f}"),
            ("Écarts détectés (moy.)", lambda a: f"{a.deviations_detected_mean:.1f}"),
            ("Actions tolérance (moy.)", lambda a: f"{a.actions_triggered_mean:.1f}"),
            ("Replans globaux (moy.)", lambda a: f"{a.replan_global_mean:.1f}"),
            ("Causes attachées (moy.)", lambda a: f"{a.causes_attached_mean:.1f}"),
            ("Événements qualité (moy.)", lambda a: f"{a.quality_events_mean:.1f}"),
            ("Coût total (€)", lambda a: f"{a.total_cost_eur_mean:.0f} ± {a.total_cost_eur_std:.0f}"),
            ("Coût par OF (€)", lambda a: f"{a.cost_per_of_eur_mean:.0f}"),
            ("Coût scrap (€)", lambda a: f"{a.cost_scrap_eur_mean:.0f}"),
        ]
        for kpi_label, fmt in rows:
            cells = [kpi_label] + [fmt(a) for a in ordered]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")

    # Résumé global : décomposition 2×2 (flux × event sourcing)
    lines.append("## Lecture globale — décomposition 2×2 (flux × event sourcing)")
    lines.append("")
    lines.append("Δ coût par doctrine vs OF (référence) :")
    lines.append("")
    lines.append("| Scénario | OF (réf) | FLUX − OF (apport flux seul) "
                 "| OF+EVENT − OF (apport event seul) "
                 "| EVENT − OF (apport combiné) |")
    lines.append("|---|---|---|---|---|")
    for scen in study.scenarios:
        agg = study.aggregates.get(scen, {})
        if DOCTRINE_OF not in agg:
            continue
        of_cost = agg[DOCTRINE_OF].total_cost_eur_mean
        cells = [f"| {scen} | {of_cost:.0f} €"]
        for d_label, d in (("flux", DOCTRINE_FLUX),
                            ("of_event", DOCTRINE_OF_EVENT),
                            ("event", DOCTRINE_EVENT)):
            if d in agg:
                delta = agg[d].total_cost_eur_mean - of_cost
                cells.append(f" | {delta:+.0f} €")
            else:
                cells.append(" | —")
        lines.append("".join(cells) + " |")
    lines.append("")
    lines.append(
        "**Lecture** : un Δ négatif signifie économie vs OF. La colonne « apport "
        "flux seul » mesure ce que la contractualisation flux apporte sans event "
        "sourcing ; la colonne « apport event seul » mesure ce que l'event sourcing "
        "apporte sans contractualisation. Si **flux seul ≈ 0** et **event seul ≈ "
        "event combiné**, on conclut que l'apport opérationnel réside dans l'event "
        "sourcing, pas dans la contractualisation."
    )
    lines.append("")
    return "\n".join(lines)
