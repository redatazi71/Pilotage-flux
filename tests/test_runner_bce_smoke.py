"""Smoke test runner — pilotages BCE.

Valide que :
  - les 2 nouvelles doctrines (of_event_bce, event_bce) sont
    dispatchables et terminent sans crash sur le scenario baseline ;
  - les hazards du scénario génèrent des delta_decisions étiquetées
    MACRS dans la base ;
  - la chaîne complète est exécutée (event_deviations + causal_events
    + delta_decisions + approval_queue selon niveau).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pilotage_flux.comparative.bce_wire import bce_kpis
from pilotage_flux.comparative.runner import run_doctrine
from pilotage_flux.comparative.scenario import (
    DOCTRINE_EVENT,
    DOCTRINE_EVENT_BCE,
    DOCTRINE_OF_EVENT,
    DOCTRINE_OF_EVENT_BCE,
    baseline_scenario,
)
from pilotage_flux.db import db_session


FIXTURES = Path("data/fixtures_v1")


@pytest.mark.skipif(not FIXTURES.exists(),
                    reason="fixtures_v1 absentes")
def test_of_event_bce_terminates_without_crash(tmp_path: Path) -> None:
    scenario = baseline_scenario()
    db = tmp_path / "bce_of_event.db"
    result = run_doctrine(
        scenario, DOCTRINE_OF_EVENT_BCE, db,
        fixtures_dir=FIXTURES,
        evaluate_rejections=False,
    )
    assert result.doctrine == DOCTRINE_OF_EVENT_BCE


@pytest.mark.skipif(not FIXTURES.exists(),
                    reason="fixtures_v1 absentes")
def test_event_bce_terminates_without_crash(tmp_path: Path) -> None:
    scenario = baseline_scenario()
    db = tmp_path / "bce_event.db"
    result = run_doctrine(
        scenario, DOCTRINE_EVENT_BCE, db,
        fixtures_dir=FIXTURES,
        evaluate_rejections=False,
    )
    assert result.doctrine == DOCTRINE_EVENT_BCE


@pytest.mark.skipif(not FIXTURES.exists(),
                    reason="fixtures_v1 absentes")
def test_event_bce_generates_delta_decisions(tmp_path: Path) -> None:
    """La chaîne hazard → MACRS → Delta doit produire des
    delta_decisions étiquetées avec racine_id et categorie_code."""
    scenario = baseline_scenario()
    db = tmp_path / "bce_decisions.db"
    run_doctrine(
        scenario, DOCTRINE_EVENT_BCE, db,
        fixtures_dir=FIXTURES,
        evaluate_rejections=False,
    )
    with db_session(db) as conn:
        kpis = bce_kpis(conn)
        # Au moins 1 décision créée à partir des hazards du scénario
        # baseline (4 aléas variés sur 15 jours).
        assert kpis["n_decisions_total"] >= 1
        # Les cellules MACRS doivent être actives (K=30 default, mais
        # baseline génère assez d'événements pour atteindre OBSERVING
        # au moins).
        n_macrs_events = kpis["macrs_events_total"]
        assert n_macrs_events >= 1


@pytest.mark.skipif(not FIXTURES.exists(),
                    reason="fixtures_v1 absentes")
def test_event_bce_does_not_break_event(tmp_path: Path) -> None:
    """La doctrine EVENT (non-BCE) reste utilisable et n'écrit pas
    dans la chaîne BCE (causal_events vide)."""
    scenario = baseline_scenario()
    db = tmp_path / "non_bce.db"
    run_doctrine(
        scenario, DOCTRINE_EVENT, db,
        fixtures_dir=FIXTURES,
        evaluate_rejections=False,
    )
    with db_session(db) as conn:
        # La doctrine EVENT (non-BCE) ne doit avoir alimenté aucune
        # causal_cell ni delta_decision.
        n_events = conn.execute(
            "SELECT COUNT(*) AS n FROM causal_events"
        ).fetchone()["n"]
        n_decisions = conn.execute(
            "SELECT COUNT(*) AS n FROM delta_decisions"
        ).fetchone()["n"]
        assert n_events == 0
        assert n_decisions == 0


@pytest.mark.skipif(not FIXTURES.exists(),
                    reason="fixtures_v1 absentes")
def test_of_event_does_not_break_event(tmp_path: Path) -> None:
    """Non-BCE OF+EVENT : pas d'écriture BCE."""
    scenario = baseline_scenario()
    db = tmp_path / "non_bce_of_event.db"
    run_doctrine(
        scenario, DOCTRINE_OF_EVENT, db,
        fixtures_dir=FIXTURES,
        evaluate_rejections=False,
    )
    with db_session(db) as conn:
        n_decisions = conn.execute(
            "SELECT COUNT(*) AS n FROM delta_decisions"
        ).fetchone()["n"]
        assert n_decisions == 0
