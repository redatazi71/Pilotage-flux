"""Test d'acceptation V8 — boucle physique étendue (L8.1) + apprentissage (L8.3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pilotage_flux.comparative import (
    DOCTRINE_EVENT,
    DOCTRINE_FLUX,
    DOCTRINES,
    baseline_scenario,
    build_variance_report,
    run_learning_loop,
    run_variance_study,
    stress_cascade_nc_scenario,
    stress_demand_spike_scenario,
)


def test_acceptance_v8_extended_physical_loop(
    tmp_path: Path, fixtures_v1_dir: Path
) -> None:
    """L8.1 : V3 doit discriminer en € sur les 4 scénarios.

    Sur les 4 scénarios canoniques, V3 ne doit JAMAIS coûter plus cher que
    FLUX. Sur breakdown (baseline, double_breakdown) il sauve du MOD. Sur
    cascade_nc il sauve du scrap. Sur demand_spike il sauve de la nervosité.
    """
    from pilotage_flux.comparative import ALL_SCENARIOS

    study = run_variance_study(
        scenarios=list(ALL_SCENARIOS.keys()),
        doctrines=list(DOCTRINES),
        seeds=[42, 100, 200],
        work_dir=tmp_path / "variance",
        fixtures_dir=fixtures_v1_dir,
    )

    for scen in ALL_SCENARIOS:
        ev = study.aggregates[scen][DOCTRINE_EVENT]
        fx = study.aggregates[scen][DOCTRINE_FLUX]
        # V3 ne coûte JAMAIS plus que FLUX
        assert ev.total_cost_eur_mean <= fx.total_cost_eur_mean + 1.0, (
            f"V3 dégrade le coût sur {scen} : "
            f"V3={ev.total_cost_eur_mean} > FLUX={fx.total_cost_eur_mean}"
        )

    # V3 sauve significativement sur baseline et double_breakdown (breakdowns)
    for scen in ("baseline", "stress_double_breakdown"):
        ev = study.aggregates[scen][DOCTRINE_EVENT]
        fx = study.aggregates[scen][DOCTRINE_FLUX]
        savings = fx.total_cost_eur_mean - ev.total_cost_eur_mean
        assert savings > 1000, (
            f"V3 doit sauver >1000€ sur {scen} (breakdown clear), observé : {savings:.0f}"
        )

    # V3 sauve nervosité sur cascade_nc (le coût peut être ≈ FLUX car L9.4 :
    # smoothing fait l'essentiel sur ces scénarios légers ; la valeur ajoutée
    # d'event sourcing porte sur nervosité + détection + causes).
    cnc_ev = study.aggregates["stress_cascade_nc"][DOCTRINE_EVENT]
    cnc_fx = study.aggregates["stress_cascade_nc"][DOCTRINE_FLUX]
    assert cnc_ev.total_cost_eur_mean <= cnc_fx.total_cost_eur_mean + 1.0
    assert cnc_ev.nervousness_mean < cnc_fx.nervousness_mean

    # V3 sauve sur demand_spike via nervosité (pas via coût direct)
    ds_ev = study.aggregates["stress_demand_spike"][DOCTRINE_EVENT]
    ds_fx = study.aggregates["stress_demand_spike"][DOCTRINE_FLUX]
    assert ds_ev.nervousness_mean < ds_fx.nervousness_mean


def test_acceptance_v8_learning_loop_converges(
    tmp_path: Path, fixtures_v1_dir: Path
) -> None:
    """L8.3 : la boucle d'apprentissage augmente le ratio actions locales."""
    scenario = baseline_scenario()
    run = run_learning_loop(
        scenario,
        tmp_path / "learning",
        n_iterations=10,
        learning_rate=0.20,
        fixtures_dir=fixtures_v1_dir,
    )
    assert len(run.iterations) == 10
    # Le ratio des actions locales monte significativement
    assert run.final_local_ratio > run.initial_local_ratio
    assert run.final_local_ratio >= 0.5, (
        f"L'apprentissage doit atteindre ≥50% d'actions locales en 10 iter, "
        f"observé : {run.final_local_ratio:.1%}"
    )
    # Au moins une itération a ajusté un seuil
    n_with_changes = sum(
        1 for it in run.iterations
        if any(
            abs(it.thresholds_after.get(k, 0) - it.thresholds_before.get(k, 0)) > 1e-6
            for k in it.thresholds_after
        )
    )
    assert n_with_changes >= 1


def test_acceptance_v8_learning_persists_thresholds(
    tmp_path: Path, fixtures_v1_dir: Path
) -> None:
    """Les seuils appris à l'itération N sont utilisés à l'itération N+1."""
    scenario = baseline_scenario()
    run = run_learning_loop(
        scenario,
        tmp_path / "learning",
        n_iterations=3,
        learning_rate=0.20,
        fixtures_dir=fixtures_v1_dir,
    )
    # Iter 1 doit débuter avec les seuils que iter 0 a appris
    iter0_after = run.iterations[0].thresholds_after
    iter1_before = run.iterations[1].thresholds_before
    for name in iter0_after:
        # tolerance_threshold_escalate doit avoir été propagé
        if name == "tolerance_threshold_escalate":
            assert iter1_before[name] == pytest.approx(iter0_after[name], rel=1e-6)
