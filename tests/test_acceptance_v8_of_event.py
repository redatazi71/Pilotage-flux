"""Test d'acceptation L8.4 — décomposition 2×2 (flux × event sourcing).

Vérifie que la 4ème doctrine OF+EVENT fonctionne et isole l'apport propre
du flux. Hypothèse forte testée : sur les scénarios opérationnels actuels,
OF+EVENT ≈ EVENT — l'apport opérationnel mesurable réside dans la couche
événementielle, pas dans la contractualisation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pilotage_flux.comparative import (
    ALL_SCENARIOS,
    DOCTRINE_EVENT,
    DOCTRINE_FLUX,
    DOCTRINE_OF,
    DOCTRINES,
    baseline_scenario,
    build_variance_report,
    compute_kpis,
    run_doctrine,
    run_variance_study,
)
from pilotage_flux.comparative.scenario import DOCTRINE_OF_EVENT


def test_l84_of_event_runs_without_error(
    tmp_path: Path, fixtures_v1_dir: Path
) -> None:
    """OF+EVENT exécute le baseline sans erreur et produit detection + causes."""
    scenario = baseline_scenario()
    result = run_doctrine(
        scenario, DOCTRINE_OF_EVENT,
        tmp_path / "of_event.db", fixtures_dir=fixtures_v1_dir,
    )
    kpi = compute_kpis(scenario, result)
    assert result.doctrine == DOCTRINE_OF_EVENT
    assert result.batch_id == "FZ-OF-VIRTUAL"
    assert kpi.of_closed > 0
    assert kpi.deviations_detected > 0, (
        "OF+EVENT doit détecter des écarts (couche événementielle active)"
    )
    assert kpi.causes_attached > 0
    assert kpi.actions_triggered > 0


def test_l84_event_dominates_of_event_when_flux_adds_value(
    tmp_path: Path, fixtures_v1_dir: Path
) -> None:
    """EVENT ≥ OF+EVENT sur les KPIs opérationnels (depuis L9.4).

    Hypothèse mise à jour (L9.4) : avec le smoothing actif, EVENT (flux+event)
    est au moins aussi bon que OF+EVENT (event seul) sur tous les KPIs. Le
    flux apporte le lissage, qui réduit la congestion goulot et donc le coût.

    Note : sur des scénarios où le smoothing ne crée pas de différence (ex :
    1 seul OF concurrent), EVENT ≈ OF+EVENT.
    """
    scenario = baseline_scenario()
    of_event = compute_kpis(
        scenario,
        run_doctrine(scenario, DOCTRINE_OF_EVENT, tmp_path / "of_event.db",
                     fixtures_dir=fixtures_v1_dir),
    )
    event = compute_kpis(
        scenario,
        run_doctrine(scenario, DOCTRINE_EVENT, tmp_path / "event.db",
                     fixtures_dir=fixtures_v1_dir),
    )
    # EVENT dominé par OF+EVENT serait une régression doctrinale grave.
    assert event.lead_time_days_avg <= of_event.lead_time_days_avg + 0.01
    assert event.wip_avg <= of_event.wip_avg + 0.01
    assert event.total_cost_eur <= of_event.total_cost_eur + 1.0
    assert event.nervousness <= of_event.nervousness + 0.01


def test_l84_variance_study_4_doctrines(
    tmp_path: Path, fixtures_v1_dir: Path
) -> None:
    """L'étude variance gère 4 doctrines proprement et produit un rapport
    avec la décomposition 2×2."""
    study = run_variance_study(
        scenarios=["baseline"],
        doctrines=list(DOCTRINES),  # 4 doctrines maintenant
        seeds=[42, 100, 200],
        work_dir=tmp_path / "variance",
        fixtures_dir=fixtures_v1_dir,
    )
    assert "baseline" in study.aggregates
    for d in DOCTRINES:
        assert d in study.aggregates["baseline"]

    report = build_variance_report(study)
    assert "OF+EVENT" in report
    assert "décomposition 2×2" in report or "decomposition" in report
    # Tous les 4 labels présents
    assert "OF " in report or " OF |" in report
    assert "FLUX" in report
    assert "EVENT" in report
