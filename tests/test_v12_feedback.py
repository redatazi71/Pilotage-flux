"""Tests V12.1.1 + V12.2.1 — Apprentissage feedback-aware."""

from __future__ import annotations

import math
import random
import sqlite3
from datetime import datetime, timedelta

import pytest

from pilotage_flux.cybernetic.delta_engine import (
    AUTONOMY_LEVEL_L3,
    reject_decision,
    submit_to_approval_queue,
)
from pilotage_flux.cybernetic.forecasting import (
    BiasCorrectionWrapper,
    HazardAwareRegressionForecaster,
    HistoricalContext,
    LinearTrendForecaster,
    rmse,
    split_holdout,
)
from pilotage_flux.cybernetic.optimization import (
    FragilityWeights,
    RejectionMemory,
)
from pilotage_flux.db import db_session


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _make_series(
    n: int = 60, trend: float = 0.5, amplitude: float = 8.0,
    noise: float = 2.5, seed: int = 42,
) -> list[float]:
    rng = random.Random(seed)
    return [
        100 + trend * t + amplitude * math.sin(2 * math.pi * t / 7)
        + rng.gauss(0, noise)
        for t in range(n)
    ]


def _seed_decision(
    conn: sqlite3.Connection,
    action_level: str = "replan_local",
    score_combined: float = 0.65,
    deviation_kind: str = "time_delta",
) -> int:
    cur = conn.execute(
        "INSERT INTO event_deviations "
        "(deviation_kind, delta_value, score, qualification) "
        "VALUES (?, 30.0, ?, 'medium')",
        (deviation_kind, score_combined),
    )
    deviation_id = int(cur.lastrowid)
    cur = conn.execute(
        """
        INSERT INTO tolerance_filter_decisions
            (deviation_id, candidate_id, score_magnitude,
             frequency_in_window, score_combined, action_level,
             latency_minutes, decided_at)
        VALUES (?, NULL, ?, 1, ?, ?, 0, datetime('now'))
        """,
        (deviation_id, score_combined, score_combined, action_level),
    )
    return int(cur.lastrowid)


# ---------------------------------------------------------------------
# V12.1.1 — HistoricalContext
# ---------------------------------------------------------------------


def test_historical_context_counts_deviations(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        for _ in range(5):
            _seed_decision(conn, deviation_kind="time_delta")
        for _ in range(2):
            _seed_decision(conn, deviation_kind="quantity_delta")
        ctx = HistoricalContext(conn)
        assert ctx.get_hazard_count("time_delta") == 5
        assert ctx.get_hazard_count("quantity_delta") == 2
        assert ctx.get_hazard_count("unknown") == 0


def test_historical_context_window_filters(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _seed_decision(conn)
        # Force la déviation à il y a 10 jours
        conn.execute(
            "UPDATE event_deviations SET detected_at = ? WHERE deviation_id = 1",
            ((datetime.utcnow() - timedelta(days=10)).isoformat(),),
        )
        ctx = HistoricalContext(conn)
        recent = ctx.get_hazard_counts_by_window("time_delta", window_days=5)
        old = ctx.get_hazard_counts_by_window("time_delta", window_days=20)
        assert recent == 0
        assert old == 1


def test_historical_context_bias_calculation() -> None:
    ctx = HistoricalContext(conn=None)  # type: ignore[arg-type]
    # Predicted 5% au-dessus
    observed = [100.0, 110.0, 120.0]
    predicted = [105.0, 115.0, 125.0]
    bias = ctx.compute_bias(observed, predicted)
    assert pytest.approx(bias, 0.001) == 5.0


def test_historical_context_bias_empty_returns_zero() -> None:
    ctx = HistoricalContext(conn=None)  # type: ignore[arg-type]
    assert ctx.compute_bias([], []) == 0.0


def test_historical_context_density_by_dow(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        # 3 déviations un lundi (weekday=0), 1 un vendredi (weekday=4)
        for d in [0, 0, 0, 4]:
            _seed_decision(conn)
            # weekday d → on prend 2026-07-06 (lundi) comme base
            conn.execute(
                "UPDATE event_deviations SET detected_at = ? "
                "WHERE deviation_id = (SELECT MAX(deviation_id) FROM event_deviations)",
                ((datetime(2026, 7, 6) + timedelta(days=d)).isoformat(),),
            )
        ctx = HistoricalContext(conn)
        density = ctx.get_hazard_density_by_day_of_week("time_delta")
        # Monday (0) doit avoir 3/4 = 0.75
        assert pytest.approx(density[0], 0.01) == 0.75
        assert pytest.approx(density[4], 0.01) == 0.25
        # Autres jours = 0
        assert density[1] == 0.0


# ---------------------------------------------------------------------
# V12.1.1 — BiasCorrectionWrapper
# ---------------------------------------------------------------------


def test_bias_correction_learn_from_observations() -> None:
    base = LinearTrendForecaster()
    bcw = BiasCorrectionWrapper(base)
    bcw.learn_bias(
        observed=[100, 110, 120],
        previous_predictions=[105, 115, 125],
    )
    assert pytest.approx(bcw.bias, 0.001) == 5.0


def test_bias_correction_subtracts_from_prediction() -> None:
    base = LinearTrendForecaster()
    bcw = BiasCorrectionWrapper(base, bias=10.0)
    bcw.fit([10.0, 20.0, 30.0, 40.0, 50.0])
    r = bcw.predict(2)
    # Base aurait prédit 60 et 70 ; après -10 → 50 et 60
    assert pytest.approx(r.values[0], abs=0.1) == 50.0
    assert pytest.approx(r.values[1], abs=0.1) == 60.0
    assert r.metadata["bias_subtracted"] == 10.0


def test_bias_correction_zero_bias_passes_through() -> None:
    base = LinearTrendForecaster()
    bcw = BiasCorrectionWrapper(base, bias=0.0)
    bcw.fit([10.0, 20.0, 30.0, 40.0, 50.0])
    r = bcw.predict(2)
    assert pytest.approx(r.values[0], abs=0.1) == 60.0


def test_bias_correction_rejects_mismatched_lengths() -> None:
    bcw = BiasCorrectionWrapper(LinearTrendForecaster())
    with pytest.raises(ValueError):
        bcw.learn_bias(observed=[1, 2], previous_predictions=[1, 2, 3])


# ---------------------------------------------------------------------
# V12.1.1 — HazardAwareRegressionForecaster
# ---------------------------------------------------------------------


def test_hazard_aware_no_context_works_like_baseline(tmp_db) -> None:
    """Sans context, doit toujours fonctionner (densité = 0 par défaut)."""
    series = _make_series(n=40)
    f = HazardAwareRegressionForecaster(context=None)
    f.fit(series)
    r = f.predict(5)
    assert len(r.values) == 5
    # density_by_dow tous 0 par défaut
    for v in r.metadata["density_by_dow"].values():
        assert v == 0.0


def test_hazard_aware_uses_context_density(tmp_db) -> None:
    """Avec context fournissant des densités non nulles, les features
    'densité' ne sont plus zéro → le forecaster les apprend."""
    with db_session(tmp_db) as conn:
        # 10 déviations le lundi
        for _ in range(10):
            _seed_decision(conn)
            conn.execute(
                "UPDATE event_deviations SET detected_at = ? "
                "WHERE deviation_id = (SELECT MAX(deviation_id) FROM event_deviations)",
                (datetime(2026, 7, 6).isoformat(),),  # lundi
            )
        ctx = HistoricalContext(conn)
        series = _make_series(n=40)
        f = HazardAwareRegressionForecaster(
            context=ctx, hazard_kind="time_delta",
            horizon_start=datetime(2026, 7, 6),
        )
        f.fit(series)
        r = f.predict(5)
        # Densité lundi est > 0
        assert r.metadata["density_by_dow"][0] > 0


# ---------------------------------------------------------------------
# V12.2.1 — RejectionMemory
# ---------------------------------------------------------------------


def test_rejection_memory_empty_db(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        memory = RejectionMemory(conn)
        assert memory.n_records == 0
        assert memory.records == []


def test_rejection_memory_loads_rejected_records(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        # 2 décisions, 1 rejetée, 1 en pending
        d1 = _seed_decision(conn, score_combined=0.65)
        d2 = _seed_decision(conn, score_combined=0.40)
        q1 = submit_to_approval_queue(conn, d1, AUTONOMY_LEVEL_L3)
        q2 = submit_to_approval_queue(conn, d2, AUTONOMY_LEVEL_L3)
        reject_decision(conn, q1, rejected_by="human:alice", notes="bad")

        memory = RejectionMemory(conn)
        assert memory.n_records == 1
        record = memory.records[0]
        assert record.queue_id == q1
        assert record.decision_id == d1
        assert record.notes == "bad"
        assert record.score_combined == 0.65


def test_rejection_memory_is_decision_id_rejected(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        d1 = _seed_decision(conn)
        q1 = submit_to_approval_queue(conn, d1, AUTONOMY_LEVEL_L3)
        reject_decision(conn, q1, rejected_by="human:alice")
        memory = RejectionMemory(conn)
        assert memory.is_decision_id_rejected(d1)
        assert not memory.is_decision_id_rejected(99999)


def test_rejection_memory_is_known_bad_pattern(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        d1 = _seed_decision(conn, score_combined=0.65,
                             deviation_kind="time_delta")
        q1 = submit_to_approval_queue(conn, d1, AUTONOMY_LEVEL_L3)
        reject_decision(conn, q1, rejected_by="human:alice")
        memory = RejectionMemory(conn)
        # Pattern proche → True
        assert memory.is_known_bad_pattern(
            deviation_kind="time_delta", score=0.70,
        )
        # Score trop différent → False
        assert not memory.is_known_bad_pattern(
            deviation_kind="time_delta", score=0.20,
        )
        # Kind différent → False
        assert not memory.is_known_bad_pattern(
            deviation_kind="quantity_delta", score=0.65,
        )


def test_rejection_memory_rejection_rate_by_level(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        for i in range(5):
            d = _seed_decision(conn)
            q = submit_to_approval_queue(conn, d, AUTONOMY_LEVEL_L3)
            if i < 2:
                reject_decision(conn, q, rejected_by="x")
            else:
                # auto_approve
                from pilotage_flux.cybernetic.delta_engine import (
                    auto_approve_with_lag,
                )
                auto_approve_with_lag(conn, q, rng=random.Random(i))
        memory = RejectionMemory(conn)
        rates = memory.rejection_rate_by_level()
        # 2 rejetés sur 5 traités → 40%
        assert pytest.approx(rates[AUTONOMY_LEVEL_L3], 0.01) == 0.40


# ---------------------------------------------------------------------
# V12.2.1 — FragilityWeights
# ---------------------------------------------------------------------


def test_fragility_weights_empty_db_returns_neutral(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        fw = FragilityWeights.from_conn(conn)
        assert fw.weights == {}
        # Tout poste inconnu = 1.0 (neutre)
        assert fw.get_weight("WS-001") == 1.0
        assert not fw.is_fragile("WS-001")


def test_fragility_weights_baseline_is_1() -> None:
    fw = FragilityWeights(weights={"WS-001": 1.0, "WS-002": 1.8})
    assert fw.get_weight("WS-001") == 1.0
    assert fw.get_weight("WS-002") == 1.8
    assert not fw.is_fragile("WS-001")
    assert fw.is_fragile("WS-002")


def test_fragility_weights_fragile_list_sorted() -> None:
    fw = FragilityWeights(weights={
        "WS-A": 1.2, "WS-B": 1.9, "WS-C": 1.4, "WS-D": 1.6,
    })
    fragile = fw.fragile_workstations(threshold=1.5)
    # 1.5+ → WS-B (1.9), WS-D (1.6), trié décroissant
    assert fragile == ["WS-B", "WS-D"]


def test_fragility_weights_caps_at_max_weight() -> None:
    """Le poids ne doit jamais dépasser max_weight."""
    # Construit manuellement pour tester la borne
    fw = FragilityWeights(weights={"WS": 2.0}, max_weight=2.0)
    assert fw.get_weight("WS") == 2.0
