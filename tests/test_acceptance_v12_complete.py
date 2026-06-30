"""Acceptance E2E V12 complet — démontre les 6 briques V12 ensemble."""

from __future__ import annotations

import math
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from pilotage_flux.cybernetic.delta_engine import (
    AUTONOMY_LEVEL_L3,
    approve_decision,
    auto_approve_with_lag,
    submit_to_approval_queue,
)
from pilotage_flux.cybernetic.forecasting import (
    BiasCorrectionWrapper,
    EnsembleForecaster,
    HazardAwareRegressionForecaster,
    HistoricalContext,
    LinearTrendForecaster,
    rmse,
    split_holdout,
)
from pilotage_flux.cybernetic.human_loop import (
    ROLE_OPERATOR,
    ROLE_SUPERVISOR,
    can_approve,
    log_audit_event,
    notify,
    set_user_role,
    snapshot_dashboard,
)
from pilotage_flux.cybernetic.human_loop.audit_log import EVENT_APPROVED
from pilotage_flux.cybernetic.human_loop.notifications import (
    KIND_PENDING_APPROVAL,
)
from pilotage_flux.cybernetic.optimization import (
    FragilityWeights,
    RejectionMemory,
)
from pilotage_flux.cybernetic.orchestration import (
    LARGE_PROFILE,
    MEDIUM_PROFILE,
    OrchestrationContext,
    OrchestrationMatrix,
    SMALL_PROFILE,
    load_profile,
    save_profile,
)
from pilotage_flux.cybernetic.orchestration.matrix import (
    FORECASTER_BIAS_CORRECTED,
    OPTIMIZER_CP_SAT,
    OPTIMIZER_HEURISTIC_ATC,
)
from pilotage_flux.db import db_session


def _seed_decision(conn: sqlite3.Connection,
                    action_level: str = "replan_local") -> int:
    cur = conn.execute(
        "INSERT INTO event_deviations "
        "(deviation_kind, delta_value, score, qualification) "
        "VALUES ('time_delta', 30.0, 0.5, 'medium')"
    )
    deviation_id = int(cur.lastrowid)
    cur = conn.execute(
        """
        INSERT INTO tolerance_filter_decisions
            (deviation_id, candidate_id, score_magnitude,
             frequency_in_window, score_combined, action_level,
             latency_minutes, decided_at)
        VALUES (?, NULL, 0.5, 1, 0.5, ?, 0, datetime('now'))
        """,
        (deviation_id, action_level),
    )
    return int(cur.lastrowid)


def test_v12_complete_e2e_all_6_bricks_together(tmp_db, tmp_path: Path) -> None:
    """E2E V12 complet : profil JSON → orchestration → forecasting feedback
    → optim feedback → delta engine → human loop → audit final.

    Démontre que les 6 briques V12 s'intègrent sans cassure.
    """
    with db_session(tmp_db) as conn:
        # ====================================================
        # BRIQUE V12.5 — Charge un profil depuis JSON
        # ====================================================
        profile_path = tmp_path / "atelier.json"
        save_profile(MEDIUM_PROFILE, profile_path)
        profile = load_profile(profile_path)
        assert profile.name == "medium"

        # ====================================================
        # BRIQUE V12.4 — Setup rôles
        # ====================================================
        set_user_role(conn, "alice@x.fr", ROLE_OPERATOR)
        set_user_role(conn, "bob@x.fr", ROLE_SUPERVISOR)

        # ====================================================
        # BRIQUE V12.1 + V12.1.1 — Forecasting avec context historique
        # ====================================================
        # Génère une série synthétique 30 jours
        random.seed(42)
        series = [
            100 + 0.5 * t + 8 * math.sin(2 * math.pi * t / 7)
            + random.gauss(0, 2.5) for t in range(30)
        ]
        train, holdout = split_holdout(series, holdout_size=5)
        ctx_hist = HistoricalContext(conn)
        bias_corrected = BiasCorrectionWrapper(
            LinearTrendForecaster(),
            bias=0.0,
        ).fit(train)
        r_forecast = bias_corrected.predict(5)
        assert len(r_forecast.values) == 5
        assert r_forecast.metadata["bias_subtracted"] == 0.0

        # ====================================================
        # BRIQUE V12.2.1 — Charge FragilityWeights (vide, DB neuve)
        # ====================================================
        fw = FragilityWeights.from_conn(
            conn,
            window_days=profile.fragility_window_days,
            max_weight=profile.fragility_max_weight,
        )
        assert fw.weights == {}  # DB neuve

        # ====================================================
        # BRIQUE V12.3 — Génère une décision L3 + enqueue
        # ====================================================
        d_id = _seed_decision(conn)
        qid = submit_to_approval_queue(conn, d_id, AUTONOMY_LEVEL_L3)
        log_audit_event(
            conn, event_type="submitted",
            actor="auto:dispatcher", queue_id=qid,
        )
        notify(
            conn, target=f"role:{ROLE_OPERATOR}",
            kind=KIND_PENDING_APPROVAL,
            message=f"Queue {qid} pending L3",
            queue_id=qid,
        )

        # ====================================================
        # BRIQUE V12.5 — Matrice d'orchestration produit la décision
        # ====================================================
        orch_ctx = OrchestrationContext(
            n_of_in_negotiable_zone=15,
            n_pending_approvals=1,
        )
        matrix = OrchestrationMatrix(profile)
        decision = matrix.decide(orch_ctx)
        assert decision.optimizer == OPTIMIZER_CP_SAT
        assert decision.autonomy_thresholds["L3"] == 0.80

        # ====================================================
        # BRIQUE V12.4 — Alice approuve via son rôle
        # ====================================================
        assert can_approve(ROLE_OPERATOR, AUTONOMY_LEVEL_L3) is True
        entry = approve_decision(
            conn, qid, approved_by="human:alice@x.fr",
            notes="V12 complet OK",
        )
        log_audit_event(
            conn, event_type=EVENT_APPROVED,
            actor="human:alice@x.fr", queue_id=qid,
            details={"lag_min": entry.approval_lag_min},
        )

        # ====================================================
        # BRIQUE V12.2.1 — Charge RejectionMemory (vide, pas de rejet)
        # ====================================================
        memory = RejectionMemory(conn)
        assert memory.n_records == 0  # rien rejeté

        # ====================================================
        # VÉRIFIE l'état final du dashboard V12.4
        # ====================================================
        snap = snapshot_dashboard(conn)
        assert snap.pending_total == 0
        assert snap.approved_last_24h == 1


def test_v12_orchestration_picks_correct_algos_in_3_scenarios(tmp_db) -> None:
    """E2E V12.5 : 3 contextes différents → 3 décisions différentes."""
    matrix_med = OrchestrationMatrix(MEDIUM_PROFILE)
    matrix_large = OrchestrationMatrix(LARGE_PROFILE)

    # Scenario 1 : petit volume + tout calme → CP-SAT + equal ensemble
    ctx1 = OrchestrationContext(n_of_in_negotiable_zone=10)
    d1 = matrix_med.decide(ctx1)
    assert d1.optimizer == OPTIMIZER_CP_SAT
    assert "equal" in d1.forecaster

    # Scenario 2 : gros volume + biais → heuristique + bias-corrected
    ctx2 = OrchestrationContext(
        n_of_in_negotiable_zone=80,
        historical_bias=8.0,
    )
    d2 = matrix_large.decide(ctx2)
    assert d2.optimizer == OPTIMIZER_HEURISTIC_ATC  # 80 > 50
    assert d2.forecaster == FORECASTER_BIAS_CORRECTED

    # Scenario 3 : 2 goulots simultanés → SLACK (DBR cassé)
    ctx3 = OrchestrationContext(
        n_of_in_negotiable_zone=15,
        has_two_bottlenecks=True,
    )
    d3 = matrix_med.decide(ctx3)
    assert "slack" in d3.optimizer.lower()
