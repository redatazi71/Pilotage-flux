"""Point 2 paper — Tests du mécanisme de rejet de SO."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from pilotage_flux.comparative.kpis import compute_kpis
from pilotage_flux.comparative.random_scenario import (
    RandomScenarioSpec,
    generate_random_scenario,
)
from pilotage_flux.comparative.runner import (
    DEFAULT_LATE_THRESHOLD_DAYS,
    _evaluate_rejections,
    run_doctrine,
)
from pilotage_flux.comparative.scenario import (
    DOCTRINE_FLUX,
    DOCTRINE_OF,
)
from pilotage_flux.data_factory import DEFAULT_SPEC, generate_random_fixtures
from pilotage_flux.db import db_session


def test_kpiset_so_fields_default() -> None:
    from pilotage_flux.comparative.kpis import KpiSet
    k = KpiSet(
        doctrine="of", scenario_name="x",
        lead_time_days_avg=5.0, lead_time_days_max=10,
        wip_avg=8.0, of_total=10, of_closed=10,
        aps_recalculations=5, deviations_detected=0,
        avg_time_deviation_minutes=None, actions_triggered=0,
        replan_local_actions=0, replan_global_actions=0,
        causes_attached=0, quality_events=0, nervousness=0.2,
    )
    # Par défaut, disponibilité = 1.0 (aucune SO traitée n'est rejetée)
    assert k.so_total == 0
    assert k.so_rejected == 0
    assert k.disponibility_so_level == 1.0


def test_evaluate_rejections_on_real_run_default_threshold() -> None:
    """Run réel : sur un scénario normal, aucune SO ne devrait être
    rejetée (livraisons dans la fenêtre)."""
    with TemporaryDirectory() as tmp:
        work = Path(tmp)
        fix_dir = work / "fix"
        generate_random_fixtures(DEFAULT_SPEC, seed=42, out_dir=fix_dir)
        scen = generate_random_scenario(
            RandomScenarioSpec(n_hazards=2),  # peu d'aléas
            seed=100, fixtures_dir=fix_dir,
        )
        result = run_doctrine(scen, DOCTRINE_FLUX, work / "test.db",
                              fixtures_dir=fix_dir)
        kpi = compute_kpis(scen, result)
        # SOs présentes, disponibilité élevée
        assert kpi.so_total > 0
        # FLUX devrait pouvoir livrer la majorité avant deadline
        assert kpi.disponibility_so_level >= 0.5


def test_evaluate_rejections_respects_late_threshold(tmp_db) -> None:
    """Vérifie qu'une SO due_date depuis trop longtemps est marquée
    cancelled si non livrée."""
    with db_session(tmp_db) as conn:
        # Setup minimal : 1 SO due il y a 30 jours, non livrée
        conn.execute(
            "INSERT OR IGNORE INTO articles (article_id, label) VALUES (?, ?)",
            ("ART-1", "Test"),
        )
        # due_date = il y a 30 jours, horizon_end = aujourd'hui
        old_due = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
        conn.execute(
            "INSERT INTO sales_orders (sales_order_id, article_id, quantity, due_date) "
            "VALUES (?, ?, ?, ?)",
            ("SO-LATE", "ART-1", 10, old_due),
        )

    # Simule un scenario terminé maintenant
    from pilotage_flux.comparative.scenario import Scenario
    scenario = Scenario(
        name="rejection_test", seed=1,
        horizon_days=30,
        horizon_start=(datetime.utcnow() - timedelta(days=30)).strftime(
            "%Y-%m-%d"
        ),
        initial_sales_orders=[], initial_stocks={},
        initial_purchase_orders=[],
    )

    from pilotage_flux.comparative.runner import RunResult
    result = RunResult(
        doctrine="of", scenario_name="rejection_test",
        db_path=tmp_db, seed=1,
    )

    _evaluate_rejections(tmp_db, scenario, result)

    with db_session(tmp_db) as conn:
        row = conn.execute(
            "SELECT status, rejected_at, rejection_reason "
            "FROM sales_orders WHERE sales_order_id = ?",
            ("SO-LATE",),
        ).fetchone()
        assert row["status"] == "cancelled"
        assert row["rejected_at"] is not None
        assert row["rejection_reason"] == "late_beyond_threshold"


def test_evaluate_rejections_skips_recently_due(tmp_db) -> None:
    """Une SO due récemment (dans la tolérance) ne doit PAS être rejetée."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO articles (article_id, label) VALUES (?, ?)",
            ("ART-1", "Test"),
        )
        # due_date = il y a 5 jours, dans la tolérance de 14 jours
        recent = (datetime.utcnow() - timedelta(days=5)).strftime("%Y-%m-%d")
        conn.execute(
            "INSERT INTO sales_orders (sales_order_id, article_id, quantity, due_date) "
            "VALUES (?, ?, ?, ?)",
            ("SO-OK", "ART-1", 10, recent),
        )

    from pilotage_flux.comparative.scenario import Scenario
    scenario = Scenario(
        name="ok_test", seed=1,
        horizon_days=10,
        horizon_start=(datetime.utcnow() - timedelta(days=10)).strftime(
            "%Y-%m-%d"
        ),
        initial_sales_orders=[], initial_stocks={},
        initial_purchase_orders=[],
    )
    from pilotage_flux.comparative.runner import RunResult
    result = RunResult(
        doctrine="of", scenario_name="ok_test",
        db_path=tmp_db, seed=1,
    )

    _evaluate_rejections(tmp_db, scenario, result)

    with db_session(tmp_db) as conn:
        row = conn.execute(
            "SELECT status, rejected_at FROM sales_orders "
            "WHERE sales_order_id = ?",
            ("SO-OK",),
        ).fetchone()
        # SO récente, non rejetée
        assert row["status"] == "open"
        assert row["rejected_at"] is None


def test_run_doctrine_opt_in_evaluate_rejections(tmp_db) -> None:
    """Le paramètre `evaluate_rejections=False` désactive la mécanique."""
    with TemporaryDirectory() as tmp:
        work = Path(tmp)
        fix_dir = work / "fix"
        generate_random_fixtures(DEFAULT_SPEC, seed=42, out_dir=fix_dir)
        # Scénario très court avec horizon dépassant les due_dates
        scen = generate_random_scenario(
            RandomScenarioSpec(n_hazards=5, horizon_days=4),
            seed=100, fixtures_dir=fix_dir,
        )
        # Mode 1 : evaluate_rejections=False
        result_no = run_doctrine(
            scen, DOCTRINE_OF, work / "no_eval.db",
            fixtures_dir=fix_dir, evaluate_rejections=False,
        )
        with db_session(result_no.db_path) as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM sales_orders "
                "WHERE rejected_at IS NOT NULL"
            ).fetchone()["n"]
            # Aucune rejection enregistrée
            assert n == 0


def test_default_late_threshold_is_14_days() -> None:
    assert DEFAULT_LATE_THRESHOLD_DAYS == 14


def test_disponibility_so_level_in_kpi(tmp_db) -> None:
    """Smoke : disponibilité_so_level est bien calculée."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO articles (article_id, label) VALUES (?, ?)",
            ("ART-1", "T"),
        )
        # 3 SOs : 2 livrées, 1 rejetée
        for i, status, rejected in [
            ("SO-A", "closed", None),
            ("SO-B", "closed", None),
            ("SO-C", "cancelled", datetime.utcnow().isoformat()),
        ]:
            conn.execute(
                "INSERT INTO sales_orders "
                "(sales_order_id, article_id, quantity, due_date, "
                " status, rejected_at) "
                "VALUES (?, ?, 1, ?, ?, ?)",
                (i, "ART-1", "2026-07-06", status, rejected),
            )

    from pilotage_flux.comparative.scenario import Scenario
    from pilotage_flux.comparative.runner import RunResult
    scen = Scenario(
        name="x", seed=1, horizon_days=5, horizon_start="2026-07-06",
        initial_sales_orders=[], initial_stocks={},
        initial_purchase_orders=[],
    )
    result = RunResult(
        doctrine="of", scenario_name="x", db_path=tmp_db, seed=1,
    )

    kpi = compute_kpis(scen, result)
    assert kpi.so_total == 3
    assert kpi.so_rejected == 1
    assert kpi.so_delivered == 2
    assert pytest.approx(kpi.disponibility_so_level, 0.001) == 2 / 3
