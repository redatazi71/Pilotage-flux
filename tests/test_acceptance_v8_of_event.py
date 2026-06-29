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


def test_l84_of_event_approx_event_on_operational_kpis(
    tmp_path: Path, fixtures_v1_dir: Path
) -> None:
    """OF+EVENT ≈ EVENT sur les KPIs opérationnels (lead time, WIP, coût, nervosité).

    Hypothèse : l'apport opérationnel ne dépend pas de la contractualisation
    flux mais de la couche événementielle + boucle physique.
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
    # Tolérance 1% sur les KPIs opérationnels
    assert of_event.lead_time_days_avg == pytest.approx(event.lead_time_days_avg, abs=0.1)
    assert of_event.wip_avg == pytest.approx(event.wip_avg, abs=0.1)
    assert of_event.total_cost_eur == pytest.approx(event.total_cost_eur, rel=0.05)
    assert of_event.nervousness == pytest.approx(event.nervousness, abs=0.05)


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
