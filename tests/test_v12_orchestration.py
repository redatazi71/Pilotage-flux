"""Tests V12.5 — Matrice d'orchestration (profile + selector)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pilotage_flux.cybernetic.orchestration import (
    DEFAULT_PROFILES,
    LARGE_PROFILE,
    MEDIUM_PROFILE,
    OrchestrationContext,
    OrchestrationMatrix,
    SMALL_PROFILE,
    WorkshopProfile,
    load_profile,
    save_profile,
)
from pilotage_flux.cybernetic.orchestration.matrix import (
    FORECASTER_BIAS_CORRECTED,
    FORECASTER_ENSEMBLE_EQUAL,
    FORECASTER_ENSEMBLE_INV_RMSE,
    FORECASTER_HAZARD_AWARE,
    OPTIMIZER_CP_SAT,
    OPTIMIZER_HEURISTIC_ATC,
    OPTIMIZER_HEURISTIC_SLACK,
)


# ---------------------------------------------------------------------
# WorkshopProfile
# ---------------------------------------------------------------------


def test_three_default_profiles_exist() -> None:
    assert set(DEFAULT_PROFILES.keys()) == {"small", "medium", "large"}


def test_default_profiles_validate() -> None:
    for p in (SMALL_PROFILE, MEDIUM_PROFILE, LARGE_PROFILE):
        p.validate()  # ne lève pas


def test_default_profiles_have_increasing_thresholds() -> None:
    """Petit atelier = seuils plus stricts ; grand = plus souples."""
    assert SMALL_PROFILE.score_threshold_L4 < MEDIUM_PROFILE.score_threshold_L4
    assert MEDIUM_PROFILE.score_threshold_L4 < LARGE_PROFILE.score_threshold_L4


def test_default_profiles_have_increasing_freeze_window() -> None:
    assert SMALL_PROFILE.freeze_window_days < MEDIUM_PROFILE.freeze_window_days
    assert MEDIUM_PROFILE.freeze_window_days < LARGE_PROFILE.freeze_window_days


def test_default_profiles_have_increasing_horizon() -> None:
    assert (
        SMALL_PROFILE.horizon_forecast_days
        < MEDIUM_PROFILE.horizon_forecast_days
    )
    assert (
        MEDIUM_PROFILE.horizon_forecast_days
        < LARGE_PROFILE.horizon_forecast_days
    )


def test_profile_rejects_non_monotonic_thresholds() -> None:
    p = WorkshopProfile(
        name="bad",
        score_threshold_L1=0.5,
        score_threshold_L2=0.4,  # < L1 → invalide
    )
    with pytest.raises(ValueError, match="L1/L2/L3/L4"):
        p.validate()


def test_profile_rejects_horizon_le_freeze() -> None:
    p = WorkshopProfile(
        name="bad",
        freeze_window_days=10,
        horizon_forecast_days=5,
    )
    with pytest.raises(ValueError, match="horizon"):
        p.validate()


def test_profile_rejects_negative_freeze() -> None:
    p = WorkshopProfile(name="bad", freeze_window_days=-1)
    with pytest.raises(ValueError, match="freeze"):
        p.validate()


def test_profile_rejects_low_fragility_max() -> None:
    p = WorkshopProfile(name="bad", fragility_max_weight=0.5)
    with pytest.raises(ValueError, match="fragility"):
        p.validate()


def test_profile_rejects_zero_timeout() -> None:
    p = WorkshopProfile(name="bad", cp_sat_timeout_sec=0.0)
    with pytest.raises(ValueError, match="timeout"):
        p.validate()


# ---------------------------------------------------------------------
# Sérialisation JSON
# ---------------------------------------------------------------------


def test_save_and_load_profile_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "profile.json"
    save_profile(MEDIUM_PROFILE, path)
    loaded = load_profile(path)
    assert loaded == MEDIUM_PROFILE


def test_save_profile_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "subdir" / "deep" / "profile.json"
    save_profile(SMALL_PROFILE, path)
    assert path.exists()


def test_load_profile_validates(tmp_path: Path) -> None:
    """Un fichier JSON avec seuils invalides doit être rejeté."""
    path = tmp_path / "bad.json"
    bad_dict = MEDIUM_PROFILE.to_dict()
    bad_dict["score_threshold_L4"] = 0.1  # < L3
    path.write_text(json.dumps(bad_dict))
    with pytest.raises(ValueError):
        load_profile(path)


# ---------------------------------------------------------------------
# OrchestrationContext
# ---------------------------------------------------------------------


def test_context_defaults() -> None:
    ctx = OrchestrationContext()
    assert ctx.n_of_in_negotiable_zone == 0
    assert ctx.n_pending_approvals == 0
    assert ctx.historical_bias == 0.0


def test_context_rejects_negative_values() -> None:
    with pytest.raises(ValueError):
        OrchestrationContext(n_of_in_negotiable_zone=-1)
    with pytest.raises(ValueError):
        OrchestrationContext(n_pending_approvals=-1)


# ---------------------------------------------------------------------
# OrchestrationMatrix — select_optimizer
# ---------------------------------------------------------------------


def test_optimizer_default_is_cp_sat() -> None:
    matrix = OrchestrationMatrix(MEDIUM_PROFILE)
    ctx = OrchestrationContext(n_of_in_negotiable_zone=10)
    opt_id, _ = matrix.select_optimizer(ctx)
    assert opt_id == OPTIMIZER_CP_SAT


def test_optimizer_falls_back_to_atc_at_scale() -> None:
    matrix = OrchestrationMatrix(MEDIUM_PROFILE)  # cp_sat_max_ofs=30
    ctx = OrchestrationContext(n_of_in_negotiable_zone=100)
    opt_id, reason = matrix.select_optimizer(ctx)
    assert opt_id == OPTIMIZER_HEURISTIC_ATC
    assert "n_ofs=100" in reason


def test_optimizer_picks_slack_on_two_bottlenecks() -> None:
    matrix = OrchestrationMatrix(MEDIUM_PROFILE)
    ctx = OrchestrationContext(
        n_of_in_negotiable_zone=10,
        has_two_bottlenecks=True,
    )
    opt_id, reason = matrix.select_optimizer(ctx)
    assert opt_id == OPTIMIZER_HEURISTIC_SLACK
    assert "2 goulots" in reason


def test_optimizer_threshold_varies_by_profile() -> None:
    """LARGE_PROFILE accepte plus d'OF en CP-SAT que MEDIUM."""
    ctx = OrchestrationContext(n_of_in_negotiable_zone=45)
    medium = OrchestrationMatrix(MEDIUM_PROFILE)  # max=30
    large = OrchestrationMatrix(LARGE_PROFILE)    # max=50
    assert medium.select_optimizer(ctx)[0] == OPTIMIZER_HEURISTIC_ATC
    assert large.select_optimizer(ctx)[0] == OPTIMIZER_CP_SAT


# ---------------------------------------------------------------------
# OrchestrationMatrix — select_forecaster
# ---------------------------------------------------------------------


def test_forecaster_default_is_ensemble_equal() -> None:
    matrix = OrchestrationMatrix(MEDIUM_PROFILE)
    ctx = OrchestrationContext()
    fc_id, _ = matrix.select_forecaster(ctx)
    assert fc_id == FORECASTER_ENSEMBLE_EQUAL


def test_forecaster_switches_to_bias_corrected_on_high_bias() -> None:
    matrix = OrchestrationMatrix(MEDIUM_PROFILE)
    ctx = OrchestrationContext(historical_bias=8.0)
    fc_id, reason = matrix.select_forecaster(ctx)
    assert fc_id == FORECASTER_BIAS_CORRECTED
    assert "biais" in reason.lower()


def test_forecaster_switches_to_hazard_aware_on_frequent_hazards() -> None:
    matrix = OrchestrationMatrix(MEDIUM_PROFILE)
    ctx = OrchestrationContext(recent_hazard_count=10)
    fc_id, reason = matrix.select_forecaster(ctx)
    assert fc_id == FORECASTER_HAZARD_AWARE
    assert "hazard" in reason


def test_forecaster_inv_rmse_on_recent_rejections() -> None:
    matrix = OrchestrationMatrix(MEDIUM_PROFILE)
    ctx = OrchestrationContext(n_recent_rejections=5)
    fc_id, _ = matrix.select_forecaster(ctx)
    assert fc_id == FORECASTER_ENSEMBLE_INV_RMSE


# ---------------------------------------------------------------------
# OrchestrationMatrix — decide()
# ---------------------------------------------------------------------


def test_decide_returns_all_fields() -> None:
    matrix = OrchestrationMatrix(MEDIUM_PROFILE)
    ctx = OrchestrationContext(n_of_in_negotiable_zone=10)
    decision = matrix.decide(ctx)
    assert decision.optimizer == OPTIMIZER_CP_SAT
    assert decision.forecaster == FORECASTER_ENSEMBLE_EQUAL
    assert set(decision.autonomy_thresholds.keys()) == {"L1", "L2", "L3", "L4"}
    assert decision.overdue_threshold_minutes == 240.0
    assert len(decision.rationale) >= 3


def test_decide_reduces_overdue_threshold_under_load() -> None:
    matrix = OrchestrationMatrix(MEDIUM_PROFILE)
    ctx = OrchestrationContext(
        n_of_in_negotiable_zone=10,
        n_pending_approvals=10,
    )
    decision = matrix.decide(ctx)
    assert decision.overdue_threshold_minutes <= 120.0


def test_decide_rationale_is_audit_friendly() -> None:
    matrix = OrchestrationMatrix(LARGE_PROFILE)
    ctx = OrchestrationContext(
        n_of_in_negotiable_zone=100,
        historical_bias=7.5,
        n_pending_approvals=6,
    )
    decision = matrix.decide(ctx)
    full_rationale = " ".join(decision.rationale)
    assert "large" in full_rationale
    assert "heuristic_atc" in full_rationale
    assert "bias" in full_rationale.lower()
    assert "overdue" in full_rationale
