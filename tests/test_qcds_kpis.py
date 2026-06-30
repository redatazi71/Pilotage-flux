"""Tests des KPIs unifiés QCDS."""

from __future__ import annotations

from pathlib import Path

import pytest

from pilotage_flux.comparative.qcds_kpis import (
    QcdsKpis,
    compute_robustesse_by_pilotage,
    extract_qcds_kpis,
)
from pilotage_flux.comparative.runner import run_doctrine
from pilotage_flux.comparative.scenario import (
    DOCTRINE_EVENT,
    DOCTRINE_OF,
    baseline_scenario,
)
from pilotage_flux.db import db_session


FIXTURES = Path("data/fixtures_v1")


# ---------------------------------------------------------------------
# extract_qcds_kpis sur run réel
# ---------------------------------------------------------------------

@pytest.mark.skipif(not FIXTURES.exists(),
                    reason="fixtures_v1 absentes")
def test_extract_returns_all_fields(tmp_path: Path) -> None:
    scenario = baseline_scenario()
    db = tmp_path / "of.db"
    result = run_doctrine(
        scenario, DOCTRINE_OF, db,
        fixtures_dir=FIXTURES, evaluate_rejections=False,
    )
    with db_session(db) as conn:
        kpis = extract_qcds_kpis(conn, result, scenario)
    assert isinstance(kpis, QcdsKpis)
    # OTIF dans [0, 1]
    assert 0.0 <= kpis.otif <= 1.0
    # Yield dans [0, 1]
    assert 0.0 <= kpis.yield_pct <= 1.0
    # WIP stats cohérents
    assert kpis.wip_mean >= 0
    assert kpis.wip_p95 >= kpis.wip_mean
    assert kpis.wip_sd >= 0
    # Lateness >= 0
    assert kpis.lateness_mean_days >= 0
    # Compteurs cohérents
    assert kpis.n_so_late >= 0
    assert kpis.n_so_total >= 0
    assert kpis.n_so_late <= kpis.n_so_total


@pytest.mark.skipif(not FIXTURES.exists(),
                    reason="fixtures_v1 absentes")
def test_to_dict_contains_keys(tmp_path: Path) -> None:
    scenario = baseline_scenario()
    db = tmp_path / "ev.db"
    result = run_doctrine(
        scenario, DOCTRINE_EVENT, db,
        fixtures_dir=FIXTURES, evaluate_rejections=False,
    )
    with db_session(db) as conn:
        kpis = extract_qcds_kpis(conn, result, scenario)
    d = kpis.to_dict()
    for k in ("otif", "yield_pct", "cost_per_good_unit",
              "wip_mean", "wip_p95", "wip_sd",
              "lead_time_mean_days", "lateness_mean_days",
              "n_so_late", "n_so_total", "n_of_closed",
              "n_hazards_observed", "mean_recovery_days",
              "n_recoveries_observed"):
        assert k in d


@pytest.mark.skipif(not FIXTURES.exists(),
                    reason="fixtures_v1 absentes")
def test_yield_close_to_one_when_no_scrap(tmp_path: Path) -> None:
    """Sur baseline avec hazards modérés, yield doit rester proche
    de 1.0."""
    scenario = baseline_scenario()
    db = tmp_path / "yield.db"
    result = run_doctrine(
        scenario, DOCTRINE_OF, db,
        fixtures_dir=FIXTURES, evaluate_rejections=False,
    )
    with db_session(db) as conn:
        kpis = extract_qcds_kpis(conn, result, scenario)
    # Yield >= 0.85 (les QUALITY_NC du baseline scrap ~10 unités)
    assert kpis.yield_pct >= 0.85


# ---------------------------------------------------------------------
# compute_robustesse_by_pilotage
# ---------------------------------------------------------------------

def test_robustesse_by_pilotage_aggregates_seeds() -> None:
    """Synthèse robustesse à partir de runs simulés (sans run réel)."""
    runs = [
        {"doctrine": "of", "saturation": 0.78, "otif": 1.0,
         "seed": 1, "status": "ok"},
        {"doctrine": "of", "saturation": 0.78, "otif": 1.0,
         "seed": 2, "status": "ok"},
        {"doctrine": "of", "saturation": 0.86, "otif": 0.95,
         "seed": 1, "status": "ok"},
        {"doctrine": "of", "saturation": 0.86, "otif": 0.93,
         "seed": 2, "status": "ok"},
        {"doctrine": "of", "saturation": 0.94, "otif": 0.85,
         "seed": 1, "status": "ok"},
        {"doctrine": "of", "saturation": 0.94, "otif": 0.83,
         "seed": 2, "status": "ok"},
    ]
    robust = compute_robustesse_by_pilotage(runs, kpi_threshold=0.90)
    assert "of" in robust
    # Mean OTIF par saturation : {0.78: 1.0, 0.86: 0.94, 0.94: 0.84}
    # Franchissement entre 0.86 et 0.94 → breaking_point ∈ (0.86, 0.94)
    assert robust["of"] is not None
    assert 0.86 < robust["of"] < 0.94


def test_robustesse_by_pilotage_empty() -> None:
    assert compute_robustesse_by_pilotage([]) == {}


def test_robustesse_by_pilotage_no_crossing() -> None:
    """Si OTIF reste >= seuil partout, breaking_point = None."""
    runs = [
        {"doctrine": "x", "saturation": s, "otif": 0.95,
         "seed": 1, "status": "ok"}
        for s in (0.78, 0.86, 0.94)
    ]
    robust = compute_robustesse_by_pilotage(runs, kpi_threshold=0.90)
    assert robust["x"] is None


def test_robustesse_by_pilotage_filters_crashed() -> None:
    """Les runs crashed sont ignorés."""
    runs = [
        {"doctrine": "x", "saturation": 0.78, "otif": 1.0,
         "seed": 1, "status": "ok"},
        {"doctrine": "x", "saturation": 0.86, "otif": 0.85,
         "seed": 1, "status": "crashed"},
        {"doctrine": "x", "saturation": 0.94, "otif": 0.95,
         "seed": 1, "status": "ok"},
    ]
    robust = compute_robustesse_by_pilotage(runs, kpi_threshold=0.90)
    # Seuls 0.78 et 0.94 sont retenus, OTIF stays >= 0.90 → None
    assert robust["x"] is None
