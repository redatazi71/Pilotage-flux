"""Option A QCDS — Tests du KPI quantity_compliance."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from pilotage_flux.comparative.kpis import KpiSet, compute_kpis
from pilotage_flux.comparative.random_scenario import (
    RandomScenarioSpec,
    generate_random_scenario,
)
from pilotage_flux.comparative.runner import RunResult, run_doctrine
from pilotage_flux.comparative.scenario import DOCTRINE_FLUX, Scenario
from pilotage_flux.data_factory import DEFAULT_SPEC, generate_random_fixtures
from pilotage_flux.db import db_session


def test_kpiset_qcds_fields_default() -> None:
    k = KpiSet(
        doctrine="of", scenario_name="x",
        lead_time_days_avg=5.0, lead_time_days_max=10,
        wip_avg=8.0, of_total=10, of_closed=10,
        aps_recalculations=5, deviations_detected=0,
        avg_time_deviation_minutes=None, actions_triggered=0,
        replan_local_actions=0, replan_global_actions=0,
        causes_attached=0, quality_events=0, nervousness=0.2,
    )
    # Défauts : 0 demande, compliance = 1.0
    assert k.qty_demanded_total == 0.0
    assert k.qty_delivered_total == 0.0
    assert k.quantity_compliance == 1.0
    assert k.n_so_underdelivered == 0
    assert k.n_so_overdelivered == 0


def test_quantity_compliance_perfect_delivery(tmp_db) -> None:
    """SO de 100, OF produit 100 → compliance = 1.0."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO articles (article_id, label) VALUES (?, ?)",
            ("ART-1", "T"),
        )
        conn.execute(
            "INSERT INTO sales_orders "
            "(sales_order_id, article_id, quantity, due_date) "
            "VALUES (?, ?, 100, ?)",
            ("SO-1", "ART-1", "2026-07-06"),
        )
        conn.execute(
            "INSERT INTO candidate_orders "
            "(candidate_id, sales_order_id, article_id, quantity, status) "
            "VALUES (?, ?, ?, 100, 'promoted')",
            ("CAND-1", "SO-1", "ART-1"),
        )
        conn.execute(
            "INSERT INTO manufacturing_orders "
            "(of_id, candidate_id, article_id, quantity, qty_good) "
            "VALUES (?, ?, ?, 100, 100)",
            ("OF-1", "CAND-1", "ART-1"),
        )

    scen = Scenario(
        name="x", seed=1, horizon_days=5, horizon_start="2026-07-06",
        initial_sales_orders=[], initial_stocks={},
        initial_purchase_orders=[],
    )
    result = RunResult(
        doctrine="of", scenario_name="x", db_path=tmp_db, seed=1,
    )
    kpi = compute_kpis(scen, result)
    assert kpi.qty_demanded_total == 100.0
    assert kpi.qty_delivered_total == 100.0
    assert kpi.quantity_compliance == 1.0
    assert kpi.n_so_underdelivered == 0
    assert kpi.n_so_overdelivered == 0


def test_quantity_compliance_underdelivered(tmp_db) -> None:
    """SO de 100, OF produit 70 → compliance = 0.7."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO articles (article_id, label) VALUES (?, ?)",
            ("ART-1", "T"),
        )
        conn.execute(
            "INSERT INTO sales_orders "
            "(sales_order_id, article_id, quantity, due_date) "
            "VALUES (?, ?, 100, ?)",
            ("SO-1", "ART-1", "2026-07-06"),
        )
        conn.execute(
            "INSERT INTO candidate_orders "
            "(candidate_id, sales_order_id, article_id, quantity, status) "
            "VALUES (?, ?, ?, 100, 'promoted')",
            ("CAND-1", "SO-1", "ART-1"),
        )
        conn.execute(
            "INSERT INTO manufacturing_orders "
            "(of_id, candidate_id, article_id, quantity, qty_good) "
            "VALUES (?, ?, ?, 100, 70)",
            ("OF-1", "CAND-1", "ART-1"),
        )

    scen = Scenario(
        name="x", seed=1, horizon_days=5, horizon_start="2026-07-06",
        initial_sales_orders=[], initial_stocks={},
        initial_purchase_orders=[],
    )
    result = RunResult(
        doctrine="of", scenario_name="x", db_path=tmp_db, seed=1,
    )
    kpi = compute_kpis(scen, result)
    assert kpi.qty_demanded_total == 100.0
    assert kpi.qty_delivered_total == 70.0
    assert pytest.approx(kpi.quantity_compliance, 0.001) == 0.7
    assert kpi.n_so_underdelivered == 1
    assert kpi.n_so_overdelivered == 0


def test_quantity_compliance_overdelivered(tmp_db) -> None:
    """SO de 100, OF produit 110 (effet lot/arrondi) → 1.1, n_over=1."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO articles (article_id, label) VALUES (?, ?)",
            ("ART-1", "T"),
        )
        conn.execute(
            "INSERT INTO sales_orders "
            "(sales_order_id, article_id, quantity, due_date) "
            "VALUES (?, ?, 100, ?)",
            ("SO-1", "ART-1", "2026-07-06"),
        )
        conn.execute(
            "INSERT INTO candidate_orders "
            "(candidate_id, sales_order_id, article_id, quantity, status) "
            "VALUES (?, ?, ?, 100, 'promoted')",
            ("CAND-1", "SO-1", "ART-1"),
        )
        conn.execute(
            "INSERT INTO manufacturing_orders "
            "(of_id, candidate_id, article_id, quantity, qty_good) "
            "VALUES (?, ?, ?, 110, 110)",
            ("OF-1", "CAND-1", "ART-1"),
        )

    scen = Scenario(
        name="x", seed=1, horizon_days=5, horizon_start="2026-07-06",
        initial_sales_orders=[], initial_stocks={},
        initial_purchase_orders=[],
    )
    result = RunResult(
        doctrine="of", scenario_name="x", db_path=tmp_db, seed=1,
    )
    kpi = compute_kpis(scen, result)
    assert pytest.approx(kpi.quantity_compliance, 0.001) == 1.1
    assert kpi.n_so_overdelivered == 1
    assert kpi.n_so_underdelivered == 0


def test_quantity_compliance_excludes_rejected_sos(tmp_db) -> None:
    """SO rejetée → exclue du compliance."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO articles (article_id, label) VALUES (?, ?)",
            ("ART-1", "T"),
        )
        # SO-1 non rejetée, livrée
        conn.execute(
            "INSERT INTO sales_orders "
            "(sales_order_id, article_id, quantity, due_date) "
            "VALUES (?, ?, 50, ?)",
            ("SO-OK", "ART-1", "2026-07-06"),
        )
        conn.execute(
            "INSERT INTO candidate_orders "
            "(candidate_id, sales_order_id, article_id, quantity, status) "
            "VALUES (?, ?, ?, 50, 'promoted')",
            ("CAND-OK", "SO-OK", "ART-1"),
        )
        conn.execute(
            "INSERT INTO manufacturing_orders "
            "(of_id, candidate_id, article_id, quantity, qty_good) "
            "VALUES (?, ?, ?, 50, 50)",
            ("OF-OK", "CAND-OK", "ART-1"),
        )
        # SO-2 REJETÉE — doit être exclue
        conn.execute(
            "INSERT INTO sales_orders "
            "(sales_order_id, article_id, quantity, due_date, rejected_at) "
            "VALUES (?, ?, 200, ?, ?)",
            ("SO-REJ", "ART-1", "2026-07-06",
             datetime.utcnow().isoformat()),
        )

    scen = Scenario(
        name="x", seed=1, horizon_days=5, horizon_start="2026-07-06",
        initial_sales_orders=[], initial_stocks={},
        initial_purchase_orders=[],
    )
    result = RunResult(
        doctrine="of", scenario_name="x", db_path=tmp_db, seed=1,
    )
    kpi = compute_kpis(scen, result)
    # Compliance compte seulement SO-OK : 50/50 = 1.0
    assert kpi.qty_demanded_total == 50.0
    assert kpi.qty_delivered_total == 50.0
    assert kpi.quantity_compliance == 1.0


def test_quantity_compliance_real_run(tmp_db) -> None:
    """Smoke test : un run réel produit un quantity_compliance plausible."""
    with TemporaryDirectory() as tmp:
        work = Path(tmp)
        fix_dir = work / "fix"
        generate_random_fixtures(DEFAULT_SPEC, seed=42, out_dir=fix_dir)
        scen = generate_random_scenario(
            RandomScenarioSpec(n_hazards=3),
            seed=100, fixtures_dir=fix_dir,
        )
        result = run_doctrine(
            scen, DOCTRINE_FLUX, work / "test.db",
            fixtures_dir=fix_dir,
        )
        kpi = compute_kpis(scen, result)
        assert kpi.qty_demanded_total > 0
        assert 0.0 <= kpi.quantity_compliance <= 1.5  # plage plausible
