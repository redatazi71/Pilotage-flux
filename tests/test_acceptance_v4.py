"""Test d'acceptation V4 — étude comparative L4.1 → L4.3.

Valide que les 3 doctrines (OF, FLUX, EVENT) exécutent le même scénario
sans erreur, et que les KPIs reflètent l'apport doctrinal attendu :

  1. V3 détecte des écarts (deviations_detected > 0) là où V1/V2 n'en
     détectent aucun.
  2. V3 attache des causes racines (causes_attached > 0).
  3. V3 produit des décisions filtre dual de tolérance (actions_triggered > 0)
     dont au moins une corrective locale.
  4. V3 a une nervosité (recalculs APS / jour) inférieure ou égale à V1+V2.
  5. Les 3 doctrines produisent le même nombre d'OF clôturés (la doctrine
     ne change pas la réalité physique, seulement ce que le système en fait).
  6. Reproductibilité : deux exécutions consécutives de la même doctrine
     produisent les mêmes KPI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pilotage_flux.comparative import (
    DOCTRINE_EVENT,
    DOCTRINE_FLUX,
    DOCTRINE_OF,
    baseline_scenario,
    build_comparative_report,
    compute_kpis,
    run_doctrine,
)


def test_acceptance_v4_three_doctrines_comparative(
    tmp_path: Path, fixtures_v1_dir: Path
) -> None:
    scenario = baseline_scenario()
    kpis = []
    for d in (DOCTRINE_OF, DOCTRINE_FLUX, DOCTRINE_EVENT):
        db = tmp_path / f"{d}.db"
        result = run_doctrine(scenario, d, db, fixtures_dir=fixtures_v1_dir)
        assert result.doctrine == d
        # Toutes les doctrines créent et clôturent des OF
        assert len(result.of_created_day) > 0
        assert len(result.of_closed_day) > 0
        kpis.append(compute_kpis(scenario, result))

    by_d = {k.doctrine: k for k in kpis}

    # Invariant 1 : V3 détecte des écarts, V1/V2 non
    assert by_d[DOCTRINE_EVENT].deviations_detected > 0, \
        "V3 doit détecter au moins un écart"
    assert by_d[DOCTRINE_OF].deviations_detected == 0
    assert by_d[DOCTRINE_FLUX].deviations_detected == 0

    # Invariant 2 : V3 attache des causes
    assert by_d[DOCTRINE_EVENT].causes_attached > 0, \
        "V3 doit attacher au moins une cause"
    assert by_d[DOCTRINE_OF].causes_attached == 0
    assert by_d[DOCTRINE_FLUX].causes_attached == 0

    # Invariant 3 : V3 produit des actions filtre dual proportionnées
    assert by_d[DOCTRINE_EVENT].actions_triggered > 0
    # Au moins une action non-globale (correct_local/replan_local/escalate),
    # sinon le filtre dual saturait tout en replan_global comme un APS naïf.
    event_kpis = by_d[DOCTRINE_EVENT]
    non_global = (
        event_kpis.actions_triggered
        - event_kpis.replan_global_actions
    )
    assert non_global > 0, \
        f"V3 doit produire des actions proportionnées (non-globales), observé : {event_kpis}"

    # Invariant 4 : V3 nervosité ≤ V1+V2
    assert by_d[DOCTRINE_EVENT].nervousness <= by_d[DOCTRINE_FLUX].nervousness
    assert by_d[DOCTRINE_EVENT].nervousness <= by_d[DOCTRINE_OF].nervousness

    # Invariant 5 : même nombre d'OF clôturés (réalité physique identique)
    closed_counts = {k.of_closed for k in kpis}
    assert len(closed_counts) == 1, \
        f"OF clôturés doivent être identiques entre doctrines, observé : {closed_counts}"

    # Rapport généré sans erreur
    report = build_comparative_report(scenario, kpis)
    assert "# Étude comparative V4" in report
    assert "OF-driven" in report
    assert "Event sourcing" in report
    assert "Hypothèse doctrinale validée" in report


def test_acceptance_v4_reproducibility(
    tmp_path: Path, fixtures_v1_dir: Path
) -> None:
    """Deux runs consécutifs de la même doctrine produisent les mêmes KPIs."""
    scenario = baseline_scenario()
    k1 = compute_kpis(
        scenario,
        run_doctrine(scenario, DOCTRINE_EVENT,
                     tmp_path / "r1.db", fixtures_dir=fixtures_v1_dir),
    )
    k2 = compute_kpis(
        scenario,
        run_doctrine(scenario, DOCTRINE_EVENT,
                     tmp_path / "r2.db", fixtures_dir=fixtures_v1_dir),
    )
    for field_name in (
        "lead_time_days_avg", "lead_time_days_max", "wip_avg",
        "of_total", "of_closed", "aps_recalculations",
        "deviations_detected", "actions_triggered",
        "replan_local_actions", "replan_global_actions",
        "causes_attached", "nervousness",
    ):
        assert getattr(k1, field_name) == getattr(k2, field_name), \
            f"Reproductibilité cassée sur {field_name}"
