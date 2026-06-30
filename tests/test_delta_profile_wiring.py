"""Tests du wiring profil Delta par pilotage."""

from __future__ import annotations

from pathlib import Path

import pytest

from pilotage_flux.comparative.bce_wire import (
    bce_kpis,
    get_tolerance_defaults_for_doctrine,
)
from pilotage_flux.comparative.runner import run_doctrine
from pilotage_flux.comparative.scenario import (
    DOCTRINE_EVENT,
    DOCTRINE_EVENT_BCE,
    DOCTRINE_OF,
    DOCTRINE_OF_EVENT,
    DOCTRINE_OF_EVENT_BCE,
    baseline_scenario,
)
from pilotage_flux.comparative.stress_scenario import stress_scenario
from pilotage_flux.cybernetic.delta_engine.tolerance_filter import (
    CONSERVATIVE_PROFILE,
)
from pilotage_flux.db import db_session


FIXTURES = Path("data/fixtures_v1")


# ---------------------------------------------------------------------
# Mapping defaults par pilotage
# ---------------------------------------------------------------------

def test_get_tolerance_defaults_for_bce_doctrines() -> None:
    of_bce = get_tolerance_defaults_for_doctrine(DOCTRINE_OF_EVENT_BCE)
    ev_bce = get_tolerance_defaults_for_doctrine(DOCTRINE_EVENT_BCE)
    # Les 5 seuils canoniques sont posés
    for d in (of_bce, ev_bce):
        assert "tolerance_threshold_watch" in d
        assert "tolerance_threshold_correct_local" in d
        assert "tolerance_threshold_replan_local" in d
        assert "tolerance_threshold_escalate" in d
        assert "tolerance_threshold_replan_global" in d


def test_get_tolerance_defaults_for_non_bce_returns_empty() -> None:
    """Les pilotages non-BCE n'imposent pas de seuils via cette voie
    (ils utilisent leurs propres defaults historiques)."""
    assert get_tolerance_defaults_for_doctrine(DOCTRINE_OF) == {}
    assert get_tolerance_defaults_for_doctrine(DOCTRINE_OF_EVENT) == {}
    assert get_tolerance_defaults_for_doctrine(DOCTRINE_EVENT) == {}


def test_bce_defaults_match_conservative_profile() -> None:
    """Les seuils BCE sont alignés avec CONSERVATIVE_PROFILE."""
    d = get_tolerance_defaults_for_doctrine(DOCTRINE_EVENT_BCE)
    assert d["tolerance_threshold_watch"] == CONSERVATIVE_PROFILE.threshold_watch
    assert d["tolerance_threshold_correct_local"] == CONSERVATIVE_PROFILE.threshold_correct_local
    assert d["tolerance_threshold_replan_local"] == CONSERVATIVE_PROFILE.threshold_replan_local
    assert d["tolerance_threshold_escalate"] == CONSERVATIVE_PROFILE.threshold_escalate
    assert d["tolerance_threshold_replan_global"] == CONSERVATIVE_PROFILE.threshold_replan_global


def test_conservative_higher_thresholds_than_historic_defaults() -> None:
    """Les seuils CONSERVATIVE doivent être supérieurs aux defaults
    historiques EVENT (qui étaient 0.20/0.50/1.00/2.00/3.50)."""
    assert CONSERVATIVE_PROFILE.threshold_watch >= 0.20
    assert CONSERVATIVE_PROFILE.threshold_correct_local >= 0.50
    assert CONSERVATIVE_PROFILE.threshold_replan_local >= 1.00


# ---------------------------------------------------------------------
# Smoke test : vérifie que les pilotages BCE produisent une
# distribution différenciée des décisions Delta
# ---------------------------------------------------------------------

@pytest.mark.skipif(not FIXTURES.exists(),
                    reason="fixtures_v1 absentes")
def test_bce_uses_conservative_thresholds_on_stress(tmp_path: Path) -> None:
    """Sur un scénario stress, le pilotage BCE doit avoir DES seuils
    sortis du DEFAULT. Vérifié par la lecture des params en DB."""
    scenario = stress_scenario(seed=42, seed_jitter=False)
    db = tmp_path / "bce_stress.db"
    run_doctrine(
        scenario, DOCTRINE_EVENT_BCE, db,
        fixtures_dir=FIXTURES, evaluate_rejections=False,
    )
    with db_session(db) as conn:
        row = conn.execute(
            "SELECT value_num FROM parameters "
            "WHERE name = 'tolerance_threshold_watch' "
            "AND valid_to IS NULL"
        ).fetchone()
        # CONSERVATIVE = 0.50, pas le default historique 0.20
        assert row["value_num"] == CONSERVATIVE_PROFILE.threshold_watch


@pytest.mark.skipif(not FIXTURES.exists(),
                    reason="fixtures_v1 absentes")
def test_event_uses_historic_thresholds(tmp_path: Path) -> None:
    """Pilotage EVENT (non-BCE) garde les seuils historiques."""
    scenario = stress_scenario(seed=42, seed_jitter=False)
    db = tmp_path / "ev_stress.db"
    run_doctrine(
        scenario, DOCTRINE_EVENT, db,
        fixtures_dir=FIXTURES, evaluate_rejections=False,
    )
    with db_session(db) as conn:
        row = conn.execute(
            "SELECT value_num FROM parameters "
            "WHERE name = 'tolerance_threshold_watch' "
            "AND valid_to IS NULL"
        ).fetchone()
        # Default EVENT historique = 0.20
        assert row["value_num"] == 0.20
