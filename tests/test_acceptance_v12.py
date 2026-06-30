"""V12.3 — Test d'acceptation E2E : workflow complet 4 niveaux d'autonomie."""

from __future__ import annotations

import random
import sqlite3

from pilotage_flux.cybernetic.delta_engine import (
    AUTONOMY_LEVEL_L1,
    AUTONOMY_LEVEL_L2,
    AUTONOMY_LEVEL_L3,
    AUTONOMY_LEVEL_L4,
    approve_decision,
    auto_approve_with_lag,
    dispatch_decision,
    list_pending,
    reject_decision,
)
from pilotage_flux.db import db_session
from pilotage_flux.events_v3.dual_tolerance import (
    ACTION_CORRECT_LOCAL,
    ACTION_INFORM,
    ACTION_REPLAN_GLOBAL,
    ACTION_REPLAN_LOCAL,
)


def _seed_decision(conn: sqlite3.Connection, action_level: str) -> int:
    """Insère une décision factice avec sa déviation."""
    cur = conn.execute(
        """
        INSERT INTO event_deviations
            (deviation_kind, delta_value, score, qualification)
        VALUES ('time_delta', 30.0, 0.5, 'medium')
        """,
    )
    deviation_id = int(cur.lastrowid)
    cur = conn.execute(
        """
        INSERT INTO tolerance_filter_decisions
            (deviation_id, candidate_id, score_magnitude,
             frequency_in_window, score_combined, action_level,
             latency_minutes, decided_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (deviation_id, None, 0.5, 1, 0.5, action_level, 0),
    )
    return int(cur.lastrowid)


def test_v12_e2e_full_4_levels_workflow(tmp_db) -> None:
    """E2E V12.3 : injecte une décision dans chacun des 4 niveaux,
    vérifie le routage, l'approbation auto et le rejet."""
    with db_session(tmp_db) as conn:
        # L1 — décision INFORM, doit être absorbée (pas de queue)
        d1 = _seed_decision(conn, ACTION_INFORM)
        r1 = dispatch_decision(conn, d1)
        assert r1.autonomy_level == AUTONOMY_LEVEL_L1
        assert r1.queue_id is None
        assert r1.immediately_actionable is True

        # L2 — décision CORRECT_LOCAL, autonome
        d2 = _seed_decision(conn, ACTION_CORRECT_LOCAL)
        r2 = dispatch_decision(conn, d2)
        assert r2.autonomy_level == AUTONOMY_LEVEL_L2
        assert r2.queue_id is None
        assert r2.immediately_actionable is True

        # L3 — REPLAN_LOCAL → queue + approbation simulation
        d3 = _seed_decision(conn, ACTION_REPLAN_LOCAL)
        r3 = dispatch_decision(conn, d3)
        assert r3.autonomy_level == AUTONOMY_LEVEL_L3
        assert r3.queue_id is not None
        assert r3.immediately_actionable is False
        # Auto-approve avec lag réaliste
        rng = random.Random(42)
        e3 = auto_approve_with_lag(conn, r3.queue_id, rng=rng)
        assert e3.status == "approved"
        assert 60.0 < e3.approval_lag_min < 500.0

        # L4 — REPLAN_GLOBAL → queue + rejet manuel
        d4 = _seed_decision(conn, ACTION_REPLAN_GLOBAL)
        r4 = dispatch_decision(conn, d4)
        assert r4.autonomy_level == AUTONOMY_LEVEL_L4
        assert r4.queue_id is not None
        # Décision rejetée par le supervisor
        e4 = reject_decision(
            conn, r4.queue_id, rejected_by="human:supervisor",
            notes="impact trop large, reformuler",
        )
        assert e4.status == "rejected"
        assert e4.notes == "impact trop large, reformuler"

        # État final : tous traités, plus de pending
        assert list_pending(conn) == []


def test_v12_e2e_volume_realistic_simulation(tmp_db) -> None:
    """E2E V12.3 : 100 décisions mixées sur les 4 niveaux, vérifie
    que la queue gère le volume sans collision et que l'auto-approve
    respecte la distribution L3/L4."""
    with db_session(tmp_db) as conn:
        rng = random.Random(123)
        action_levels = [
            ACTION_INFORM, ACTION_CORRECT_LOCAL,
            ACTION_REPLAN_LOCAL, ACTION_REPLAN_GLOBAL,
        ]
        results: dict[str, int] = {}
        queue_ids: list[int] = []
        for _ in range(100):
            action = rng.choice(action_levels)
            d_id = _seed_decision(conn, action)
            res = dispatch_decision(conn, d_id)
            results[res.autonomy_level] = results.get(res.autonomy_level, 0) + 1
            if res.queue_id is not None:
                queue_ids.append(res.queue_id)

        # Vérifie qu'on a au moins 1 de chaque niveau
        assert AUTONOMY_LEVEL_L1 in results
        assert AUTONOMY_LEVEL_L2 in results
        assert AUTONOMY_LEVEL_L3 in results
        assert AUTONOMY_LEVEL_L4 in results

        # Auto-approve toutes les pending et vérifie qu'aucune n'est perdue
        n_pending = len(queue_ids)
        for qid in queue_ids:
            auto_approve_with_lag(conn, qid, rng=rng)
        assert len(list_pending(conn)) == 0
        # Vérifie que toutes ont un lag mesuré > 0
        approved = conn.execute(
            "SELECT approval_lag_min FROM approval_queue "
            "WHERE status = 'approved'"
        ).fetchall()
        assert len(approved) == n_pending
        for r in approved:
            assert r["approval_lag_min"] > 0


def test_v12_e2e_idempotence_under_replay(tmp_db) -> None:
    """E2E V12.3 : dispatch deux fois la même décision L3 — la queue
    ne doit pas avoir 2 entrées (idempotence sous replay event sourcing)."""
    with db_session(tmp_db) as conn:
        d_id = _seed_decision(conn, ACTION_REPLAN_LOCAL)
        r1 = dispatch_decision(conn, d_id)
        r2 = dispatch_decision(conn, d_id)
        r3 = dispatch_decision(conn, d_id)
        assert r1.queue_id == r2.queue_id == r3.queue_id
        # Une seule entrée dans la queue
        rows = conn.execute(
            "SELECT COUNT(*) AS n FROM approval_queue WHERE decision_id = ?",
            (d_id,),
        ).fetchone()
        assert rows["n"] == 1
