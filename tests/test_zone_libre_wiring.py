"""Zone libre — wiring forecast deviation dans la chaîne BCE."""

from __future__ import annotations

from pathlib import Path

import pytest

from pilotage_flux.comparative.bce_wire import bce_kpis
from pilotage_flux.comparative.runner import run_doctrine
from pilotage_flux.comparative.scenario import (
    DOCTRINE_EVENT_BCE,
    HAZARD_BREAKDOWN,
    HAZARD_PO_DELAY,
    HAZARD_QUALITY_NC,
    HAZARD_URGENT_ORDER,
    HazardEvent,
    baseline_scenario,
)
from pilotage_flux.cybernetic.delta_engine.levels import (
    seed_default_delta_levels,
)
from pilotage_flux.cybernetic.macrs.couche2 import init_cells_from_layer1
from pilotage_flux.cybernetic.macrs.forecast_emission import (
    HAZARD_TO_FORECAST_RACINE,
    ZONE_LIBRE_RACINES,
    count_zone_libre_decisions,
    emit_forecast_deviation,
)
from pilotage_flux.db import db_session


FIXTURES = Path("data/fixtures_v1")


# ---------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------

def test_zone_libre_racines_canonical() -> None:
    """Racines forecast doctrinales : R002, R003, R017."""
    assert ZONE_LIBRE_RACINES == ("R002", "R003", "R017")


def test_forecast_mapping_canonical() -> None:
    """URGENT_ORDER et PO_DELAY → R017 Erreur de couverture (Mat)."""
    assert HAZARD_TO_FORECAST_RACINE[HAZARD_URGENT_ORDER] == ("R017", "Mat")
    assert HAZARD_TO_FORECAST_RACINE[HAZARD_PO_DELAY] == ("R017", "Mat")


def test_no_forecast_mapping_for_non_demand_hazards() -> None:
    """Les hazards non-forecast ne sont pas dans le mapping."""
    assert HAZARD_BREAKDOWN not in HAZARD_TO_FORECAST_RACINE
    assert HAZARD_QUALITY_NC not in HAZARD_TO_FORECAST_RACINE


# ---------------------------------------------------------------------
# emit_forecast_deviation
# ---------------------------------------------------------------------

def _setup_bce(conn) -> None:
    init_cells_from_layer1(conn)
    seed_default_delta_levels(conn)


def test_emit_forecast_creates_event_deviation(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _setup_bce(conn)
        h = HazardEvent(
            day=3, kind=HAZARD_URGENT_ORDER,
            payload={"sales_order_id": "SO-URG"},
        )
        res = emit_forecast_deviation(
            conn, h,
            occurred_at="2026-07-10T08:00:00",
            decided_at="2026-07-10T08:05:00",
        )
        assert res.deviation_id is not None
        row = conn.execute(
            "SELECT qualification, deviation_kind FROM event_deviations "
            "WHERE deviation_id = ?",
            (res.deviation_id,),
        ).fetchone()
        assert row["qualification"] == "forecast"
        assert row["deviation_kind"] == "forecast_delta"


def test_emit_forecast_targets_R017_for_urgent_order(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _setup_bce(conn)
        h = HazardEvent(day=3, kind=HAZARD_URGENT_ORDER, payload={})
        res = emit_forecast_deviation(
            conn, h,
            occurred_at="2026-07-10T08:00:00",
            decided_at="2026-07-10T08:05:00",
        )
        assert res.racine_id == "R017"
        assert res.categorie_code == "Mat"
        # Delta decision créée + lié à R017
        row = conn.execute(
            "SELECT racine_id, categorie_code FROM delta_decisions "
            "WHERE delta_decision_id = ?",
            (res.delta_decision_id,),
        ).fetchone()
        assert row["racine_id"] == "R017"
        assert row["categorie_code"] == "Mat"


def test_emit_forecast_skip_for_non_forecast_hazard(tmp_db) -> None:
    """HAZARD_BREAKDOWN n'a pas de mapping forecast → skipped sans
    erreur."""
    with db_session(tmp_db) as conn:
        _setup_bce(conn)
        h = HazardEvent(day=3, kind=HAZARD_BREAKDOWN, payload={})
        res = emit_forecast_deviation(
            conn, h,
            occurred_at="2026-07-10T08:00:00",
            decided_at="2026-07-10T08:05:00",
        )
        assert res.skipped_reason == "no_forecast_mapping"
        assert res.deviation_id is None
        assert res.delta_decision_id is None


def test_emit_forecast_alimente_macrs_cell_R017(tmp_db) -> None:
    """La cellule (R017, Mat) doit être touchée par l'émission."""
    with db_session(tmp_db) as conn:
        _setup_bce(conn)
        h = HazardEvent(day=3, kind=HAZARD_URGENT_ORDER, payload={})
        emit_forecast_deviation(
            conn, h,
            occurred_at="2026-07-10T08:00:00",
            decided_at="2026-07-10T08:05:00",
        )
        row = conn.execute(
            "SELECT n_events_total, status FROM causal_cells "
            "WHERE racine_id='R017' AND categorie_code='Mat'"
        ).fetchone()
        assert row["n_events_total"] == 1
        assert row["status"] in ("OBSERVING", "ACTIVE")


def test_emit_forecast_score_clamped(tmp_db) -> None:
    """impact_score > 1 → clamp à 1.0."""
    with db_session(tmp_db) as conn:
        _setup_bce(conn)
        h = HazardEvent(day=3, kind=HAZARD_URGENT_ORDER, payload={})
        res = emit_forecast_deviation(
            conn, h,
            occurred_at="2026-07-10T08:00:00",
            decided_at="2026-07-10T08:05:00",
            impact_score=3.0,
        )
        row = conn.execute(
            "SELECT score FROM event_deviations WHERE deviation_id = ?",
            (res.deviation_id,),
        ).fetchone()
        assert row["score"] == 1.0


# ---------------------------------------------------------------------
# count_zone_libre_decisions
# ---------------------------------------------------------------------

def test_count_zone_libre_filters_by_racine(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _setup_bce(conn)
        # Émet 2 forecasts (R017) + 1 BREAKDOWN qui touche R030 (hors
        # zone libre)
        for kind in (HAZARD_URGENT_ORDER, HAZARD_URGENT_ORDER):
            h = HazardEvent(day=3, kind=kind, payload={})
            emit_forecast_deviation(
                conn, h,
                occurred_at=f"2026-07-10T08:00:00",
                decided_at=f"2026-07-10T08:05:00",
            )
        zl = count_zone_libre_decisions(conn)
        assert zl["n_total"] == 2
        assert zl["by_racine"].get("R017") == 2
        # Pas de decisions sur R030 (hors zone libre)
        assert "R030" not in zl["by_racine"]


def test_count_zone_libre_empty_db(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        zl = count_zone_libre_decisions(conn)
        assert zl["n_total"] == 0
        assert zl["by_racine"] == {}


# ---------------------------------------------------------------------
# Smoke test runner BCE : zone libre se peuple sur urgent_order
# ---------------------------------------------------------------------

@pytest.mark.skipif(not FIXTURES.exists(),
                    reason="fixtures_v1 absentes")
def test_runner_event_bce_populates_zone_libre(tmp_path: Path) -> None:
    """Le scénario baseline a 1 urgent_order → la zone libre doit
    avoir au moins 1 decision (R017)."""
    scenario = baseline_scenario()
    db = tmp_path / "zl.db"
    run_doctrine(
        scenario, DOCTRINE_EVENT_BCE, db,
        fixtures_dir=FIXTURES,
        evaluate_rejections=False,
    )
    with db_session(db) as conn:
        kpis = bce_kpis(conn)
        # Au moins 1 décision sur R017
        assert kpis["zone_libre_n_decisions"] >= 1
        assert kpis["zone_libre_by_racine"].get("R017", 0) >= 1


@pytest.mark.skipif(not FIXTURES.exists(),
                    reason="fixtures_v1 absentes")
def test_runner_non_bce_does_not_populate_zone_libre(tmp_path: Path) -> None:
    """Doctrine non-BCE → aucune décision zone libre."""
    from pilotage_flux.comparative.scenario import DOCTRINE_EVENT
    scenario = baseline_scenario()
    db = tmp_path / "zl_non_bce.db"
    run_doctrine(
        scenario, DOCTRINE_EVENT, db,
        fixtures_dir=FIXTURES,
        evaluate_rejections=False,
    )
    with db_session(db) as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM delta_decisions"
        ).fetchone()["n"]
        assert n == 0
