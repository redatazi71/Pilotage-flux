"""V12.2 — Acceptance E2E : pipeline complet zone négociable + intégration V12.3."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from pilotage_flux.cybernetic.delta_engine import (
    AUTONOMY_LEVEL_L3,
    list_pending,
    submit_to_approval_queue,
)
from pilotage_flux.cybernetic.optimization import (
    propose_dynamic_replan,
    resolve_negotiable_zone,
)
from pilotage_flux.db import db_session
from pilotage_flux.events_v3.dual_tolerance import ACTION_REPLAN_LOCAL


def _seed_horizon(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO run_metadata (key, value) VALUES (?, ?)",
        ("horizon_start", "2026-07-06"),
    )


def _seed_of(
    conn: sqlite3.Connection, of_id: str, article: str,
    days_from_start: int, due_days_offset: int = 14,
) -> None:
    base = datetime.fromisoformat("2026-07-06")
    planned = (base + timedelta(days=days_from_start)).isoformat()
    due = (base + timedelta(days=due_days_offset)).strftime("%Y-%m-%d")
    conn.execute(
        "INSERT OR IGNORE INTO articles (article_id, label) VALUES (?, ?)",
        (article, article),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO sales_orders
            (sales_order_id, article_id, quantity, due_date)
        VALUES (?, ?, 10, ?)
        """,
        (f"SO-{of_id}", article, due),
    )
    conn.execute(
        """
        INSERT INTO candidate_orders
            (candidate_id, sales_order_id, article_id, quantity, status, zone)
        VALUES (?, ?, ?, 10, 'promoted', 'libre')
        """,
        (f"CAND-{of_id}", f"SO-{of_id}", article),
    )
    conn.execute(
        """
        INSERT INTO manufacturing_orders
            (of_id, candidate_id, article_id, quantity, status, planned_start)
        VALUES (?, ?, ?, 10, 'created', ?)
        """,
        (of_id, f"CAND-{of_id}", article, planned),
    )


def _seed_op(
    conn: sqlite3.Connection, of_id: str, ws: str,
    unit_time_min: int, seq: int = 1,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO workstations (workstation_id, label, sequence_idx) "
        "VALUES (?, ?, ?)",
        (ws, ws, seq),
    )
    conn.execute(
        """
        INSERT INTO order_operations
            (of_id, sequence_idx, workstation_id, unit_time_min)
        VALUES (?, ?, ?, ?)
        """,
        (of_id, seq, ws, unit_time_min),
    )


def test_v12_2_e2e_pipeline_zone_to_replan(tmp_db) -> None:
    """E2E : seed 6 OFs (2 freeze, 3 négociable, 1 libre)
    → resolve zone → propose_dynamic_replan → vérifie résultat."""
    with db_session(tmp_db) as conn:
        _seed_horizon(conn)
        # 2 OFs en freeze (jours 1, 3)
        _seed_of(conn, "OF-F1", "ART-A", days_from_start=1, due_days_offset=8)
        _seed_op(conn, "OF-F1", "WS-001", 240)
        _seed_of(conn, "OF-F2", "ART-B", days_from_start=3, due_days_offset=10)
        _seed_op(conn, "OF-F2", "WS-001", 240)
        # 3 OFs en zone négociable (jours 8, 12, 18)
        _seed_of(conn, "OF-N1", "ART-C", days_from_start=8, due_days_offset=15)
        _seed_op(conn, "OF-N1", "WS-002", 480)
        _seed_of(conn, "OF-N2", "ART-D", days_from_start=12, due_days_offset=20)
        _seed_op(conn, "OF-N2", "WS-002", 360)
        _seed_of(conn, "OF-N3", "ART-E", days_from_start=18, due_days_offset=25)
        _seed_op(conn, "OF-N3", "WS-002", 480)
        # 1 OF au-delà de l'horizon de forecast
        _seed_of(conn, "OF-L1", "ART-F", days_from_start=35, due_days_offset=42)
        _seed_op(conn, "OF-L1", "WS-002", 240)

        zone = resolve_negotiable_zone(
            conn, reference_day=0, freeze_window_days=5,
            horizon_forecast_days=28,
        )
        # Vérifie la sélection
        assert len(zone.of_ids_in_zone) == 3
        assert "OF-N1" in zone.of_ids_in_zone
        assert "OF-N2" in zone.of_ids_in_zone
        assert "OF-N3" in zone.of_ids_in_zone
        assert "OF-F1" not in zone.of_ids_in_zone
        assert "OF-L1" not in zone.of_ids_in_zone

        # CP-SAT propose un re-plan
        result = propose_dynamic_replan(conn, zone, timeout_sec=5.0)
        assert result.status in {"optimal", "feasible", "fallback_slack"}
        assert len(result.new_launch_day_by_of) == 3
        # Tous les jours proposés sont dans la zone négociable
        for of_id, day in result.new_launch_day_by_of.items():
            assert 5 <= day < 28, f"{of_id}: jour {day} hors zone"


def test_v12_2_e2e_integration_with_v12_3_approval(tmp_db) -> None:
    """E2E : Delta engine déclenche un L3 → V12.2 propose un replan
    → l'OF cible est dans la queue d'approbation."""
    with db_session(tmp_db) as conn:
        _seed_horizon(conn)
        # 2 OFs dans la zone négociable
        _seed_of(conn, "OF-A", "ART-A", days_from_start=8, due_days_offset=15)
        _seed_op(conn, "OF-A", "WS-001", 240)
        _seed_of(conn, "OF-B", "ART-B", days_from_start=15, due_days_offset=22)
        _seed_op(conn, "OF-B", "WS-001", 240)

        # 1. V12.3 enregistre une déviation + décision REPLAN_LOCAL
        cur = conn.execute(
            "INSERT INTO event_deviations "
            "(deviation_kind, delta_value, score, qualification) "
            "VALUES ('time_delta', 45.0, 0.65, 'high')"
        )
        deviation_id = int(cur.lastrowid)
        cur = conn.execute(
            """
            INSERT INTO tolerance_filter_decisions
                (deviation_id, candidate_id, score_magnitude,
                 frequency_in_window, score_combined, action_level,
                 latency_minutes, decided_at)
            VALUES (?, NULL, 0.65, 1, 0.65, ?, 0, datetime('now'))
            """,
            (deviation_id, ACTION_REPLAN_LOCAL),
        )
        decision_id = int(cur.lastrowid)

        # 2. V12.3 enqueue la décision pour approbation
        queue_id = submit_to_approval_queue(
            conn, decision_id, AUTONOMY_LEVEL_L3,
            notes="V12.2 replan suggested",
        )
        assert queue_id > 0

        # 3. V12.2 propose le replan en parallèle
        zone = resolve_negotiable_zone(
            conn, reference_day=0, freeze_window_days=5,
            horizon_forecast_days=28,
        )
        replan = propose_dynamic_replan(conn, zone, timeout_sec=5.0)
        assert replan.status in {"optimal", "feasible", "fallback_slack"}

        # 4. La queue contient bien la décision pending,
        # le replan contient la proposition associée
        pending = list_pending(conn)
        assert len(pending) == 1
        assert pending[0].queue_id == queue_id
        assert len(replan.new_launch_day_by_of) == 2
