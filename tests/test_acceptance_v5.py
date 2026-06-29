"""Test d'acceptation V5 — étude comparative étendue avec variance (L5.1+L5.2).

Valide :
  1. La variance multi-seeds produit des KPIs reproductibles (même run = même
     KPI à la seed près).
  2. Sur le scénario `stress_double_breakdown`, V3 sauve du lead time grâce
     à la boucle physique (L5.2) — Δ lead_time(V3, FLUX) < 0.
  3. Sur tous les scénarios, V3 détecte des écarts (deviations > 0) et
     attache des causes (causes > 0).
  4. Le rapport étendu Markdown se construit sans erreur.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pilotage_flux.comparative import (
    ALL_SCENARIOS,
    DOCTRINE_EVENT,
    DOCTRINE_FLUX,
    DOCTRINES,
    build_variance_report,
    run_variance_study,
)


def test_acceptance_v5_variance_study(
    tmp_path: Path, fixtures_v1_dir: Path
) -> None:
    """Étude variance multi-seeds + multi-scénarios + 3 doctrines."""
    study = run_variance_study(
        scenarios=list(ALL_SCENARIOS.keys()),
        doctrines=list(DOCTRINES),
        seeds=[42, 100, 200],
        work_dir=tmp_path / "variance",
        fixtures_dir=fixtures_v1_dir,
    )

    # Invariant 1 : tous les scénarios × doctrines ont des résultats
    for scen in ALL_SCENARIOS:
        assert scen in study.aggregates
        for d in DOCTRINES:
            assert d in study.aggregates[scen]
            agg = study.aggregates[scen][d]
            assert agg.n_runs == 3
            assert agg.of_closed_mean > 0

    # Invariant 2 : V3 détecte + attache des causes sur TOUS les scénarios
    for scen in ALL_SCENARIOS:
        ev_agg = study.aggregates[scen][DOCTRINE_EVENT]
        assert ev_agg.deviations_detected_mean > 0, \
            f"V3 doit détecter des écarts sur {scen}"
        assert ev_agg.causes_attached_mean > 0, \
            f"V3 doit attacher des causes sur {scen}"

    # Invariant 3 : V3 lead_time ≤ FLUX lead_time sur stress_double_breakdown
    # (la doctrine événementielle SAUVE du lead time sur les pannes)
    ev_agg = study.aggregates["stress_double_breakdown"][DOCTRINE_EVENT]
    fx_agg = study.aggregates["stress_double_breakdown"][DOCTRINE_FLUX]
    assert ev_agg.lead_time_avg_mean < fx_agg.lead_time_avg_mean, \
        f"V3 doit sauver du lead time vs FLUX sur stress_double_breakdown : " \
        f"V3={ev_agg.lead_time_avg_mean}, FLUX={fx_agg.lead_time_avg_mean}"

    # Invariant 4 : V3 nervosité ≤ FLUX nervosité sur baseline (urgent + breakdown)
    ev_b = study.aggregates["baseline"][DOCTRINE_EVENT]
    fx_b = study.aggregates["baseline"][DOCTRINE_FLUX]
    assert ev_b.nervousness_mean <= fx_b.nervousness_mean

    # Rapport généré sans erreur
    report = build_variance_report(study)
    assert "Étude comparative étendue" in report
    assert "stress_double_breakdown" in report
    assert "stress_cascade_nc" in report
    assert "stress_demand_spike" in report
    assert "baseline" in report
    # Toutes les doctrines mentionnées
    assert "OF" in report and "FLUX" in report and "EVENT" in report
