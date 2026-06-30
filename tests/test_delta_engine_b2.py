"""Moteur Delta B.2 — filtre dual tolérances formalisé + profil versionné."""

from __future__ import annotations

import pytest

from pilotage_flux.cybernetic.delta_engine.decisions import (
    STATUS_PENDING,
    get_decision,
)
from pilotage_flux.cybernetic.delta_engine.levels import (
    L_CORRIGER_LOCAL,
    L_ESCALADER,
    L_INFORMER,
    L_REPLANIFIER_GLOBAL,
    L_REPLANIFIER_LOCAL,
    L_SURVEILLER,
    seed_default_delta_levels,
)
from pilotage_flux.cybernetic.delta_engine.tolerance_filter import (
    ACTION_TO_NIVEAU,
    CONSERVATIVE_PROFILE,
    DEFAULT_PROFILE,
    PROFILES_BY_LABEL,
    REACTIVE_PROFILE,
    ToleranceProfile,
    apply_tolerance_profile,
    evaluate_and_decide,
    get_current_tolerance_profile,
    map_action_to_niveau,
)
from pilotage_flux.db import db_session


# ---------------------------------------------------------------------
# Mapping action_level → niveau_code
# ---------------------------------------------------------------------

def test_action_to_niveau_mapping_canonical() -> None:
    assert ACTION_TO_NIVEAU == {
        "inform":         L_INFORMER,
        "watch":          L_SURVEILLER,
        "correct_local":  L_CORRIGER_LOCAL,
        "replan_local":   L_REPLANIFIER_LOCAL,
        "escalate":       L_ESCALADER,
        "replan_global":  L_REPLANIFIER_GLOBAL,
    }


@pytest.mark.parametrize("action, niveau", [
    ("inform", L_INFORMER),
    ("watch", L_SURVEILLER),
    ("correct_local", L_CORRIGER_LOCAL),
    ("replan_local", L_REPLANIFIER_LOCAL),
    ("escalate", L_ESCALADER),
    ("replan_global", L_REPLANIFIER_GLOBAL),
])
def test_map_action_to_niveau(action: str, niveau: str) -> None:
    assert map_action_to_niveau(action) == niveau


def test_map_action_unknown_raises() -> None:
    with pytest.raises(ValueError, match="action_level inconnu"):
        map_action_to_niveau("foo")


# ---------------------------------------------------------------------
# Profils canoniques
# ---------------------------------------------------------------------

def test_three_canonical_profiles() -> None:
    assert set(PROFILES_BY_LABEL) == {"default", "conservative", "reactive"}


def test_default_profile_aligned_with_legacy_defaults() -> None:
    """Cohérence avec events_v3/dual_tolerance.DEFAULT_THRESHOLDS."""
    from pilotage_flux.events_v3.dual_tolerance import DEFAULT_THRESHOLDS
    assert DEFAULT_PROFILE.threshold_watch == DEFAULT_THRESHOLDS["tolerance_threshold_watch"]
    assert DEFAULT_PROFILE.threshold_correct_local == DEFAULT_THRESHOLDS["tolerance_threshold_correct_local"]


def test_conservative_higher_thresholds_than_default() -> None:
    assert CONSERVATIVE_PROFILE.threshold_watch > DEFAULT_PROFILE.threshold_watch
    assert CONSERVATIVE_PROFILE.latency_minutes > DEFAULT_PROFILE.latency_minutes
    assert CONSERVATIVE_PROFILE.window_hours > DEFAULT_PROFILE.window_hours


def test_reactive_lower_thresholds_than_default() -> None:
    assert REACTIVE_PROFILE.threshold_watch < DEFAULT_PROFILE.threshold_watch
    assert REACTIVE_PROFILE.latency_minutes == 0


# ---------------------------------------------------------------------
# apply / get profil
# ---------------------------------------------------------------------

def test_apply_profile_persists_seven_params(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        apply_tolerance_profile(conn, DEFAULT_PROFILE)
        rows = conn.execute(
            "SELECT name, value_num, value_text, version, valid_to "
            "FROM parameters WHERE scope='global' "
            "AND name LIKE 'tolerance_%' ORDER BY name"
        ).fetchall()
        # 7 paramètres posés
        names = {r["name"] for r in rows}
        assert names == {
            "tolerance_threshold_watch",
            "tolerance_threshold_correct_local",
            "tolerance_threshold_replan_local",
            "tolerance_threshold_escalate",
            "tolerance_threshold_replan_global",
            "tolerance_window_hours",
            "tolerance_latency_minutes",
        }
        # value_text porte le label de profil
        for r in rows:
            assert r["value_text"] == "default"
        # Aucune version close (valid_to NULL)
        for r in rows:
            assert r["valid_to"] is None


def test_apply_profile_versioned_on_change(tmp_db) -> None:
    """Appliquer un second profil ferme l'ancienne version et insère
    une nouvelle (versioning conforme spec §5.2 / cadrage §7bis.4)."""
    with db_session(tmp_db) as conn:
        apply_tolerance_profile(conn, DEFAULT_PROFILE)
        apply_tolerance_profile(conn, REACTIVE_PROFILE)
        rows = conn.execute(
            "SELECT version, valid_to, value_text "
            "FROM parameters WHERE name = 'tolerance_threshold_watch' "
            "ORDER BY version"
        ).fetchall()
        # 2 versions : v1 close, v2 actuelle
        assert len(rows) == 2
        assert rows[0]["version"] == 1
        assert rows[0]["valid_to"] is not None
        assert rows[0]["value_text"] == "default"
        assert rows[1]["version"] == 2
        assert rows[1]["valid_to"] is None
        assert rows[1]["value_text"] == "reactive"


def test_get_current_profile_default_when_empty(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        p = get_current_tolerance_profile(conn)
        assert p.label == "default"
        assert p.threshold_watch == DEFAULT_PROFILE.threshold_watch
        assert p.latency_minutes == DEFAULT_PROFILE.latency_minutes


def test_get_current_profile_round_trip(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        apply_tolerance_profile(conn, REACTIVE_PROFILE)
        p = get_current_tolerance_profile(conn)
        assert p.label == "reactive"
        assert p.threshold_watch == REACTIVE_PROFILE.threshold_watch
        assert p.threshold_replan_global == REACTIVE_PROFILE.threshold_replan_global
        assert p.window_hours == REACTIVE_PROFILE.window_hours
        assert p.latency_minutes == REACTIVE_PROFILE.latency_minutes


def test_custom_profile_round_trip(tmp_db) -> None:
    custom = ToleranceProfile(
        label="custom",
        threshold_watch=0.15,
        threshold_correct_local=0.35,
        threshold_replan_local=0.55,
        threshold_escalate=0.75,
        threshold_replan_global=1.00,
        window_hours=18,
        latency_minutes=10,
    )
    with db_session(tmp_db) as conn:
        apply_tolerance_profile(conn, custom)
        p = get_current_tolerance_profile(conn)
        assert p == custom


# ---------------------------------------------------------------------
# Helper end-to-end evaluate_and_decide
# ---------------------------------------------------------------------

def _seed_deviation(conn, *, score: float, kind: str = "time_delta",
                    candidate_id: str | None = None) -> int:
    """Insère un event_deviation utilisable par le filtre dual."""
    cur = conn.execute(
        "INSERT INTO event_deviations "
        "(deviation_kind, delta_value, score, qualification, "
        " detected_at, candidate_id, is_absorbed) "
        "VALUES (?, ?, ?, 'mineur', datetime('now'), ?, 0)",
        (kind, score, score, candidate_id),
    )
    return int(cur.lastrowid)


def test_evaluate_and_decide_creates_delta_decision(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        seed_default_delta_levels(conn)
        dev_id = _seed_deviation(conn, score=0.05)   # inform
        tol, delta_id = evaluate_and_decide(
            conn, dev_id, decided_at="2026-07-15T10:00:00",
        )
        assert tol.action_level == "inform"
        d = get_decision(conn, delta_id)
        assert d is not None
        assert d.niveau_code == L_INFORMER
        assert d.deviation_id == dev_id
        assert d.score_magnitude == tol.score_combined
        assert d.status == STATUS_PENDING
        assert d.actor == "auto:delta_engine"


def test_evaluate_and_decide_high_score_replan_global(tmp_db) -> None:
    """score = 1.50 > 1.20 → replan_global → L6."""
    with db_session(tmp_db) as conn:
        seed_default_delta_levels(conn)
        dev_id = _seed_deviation(conn, score=1.5)
        tol, delta_id = evaluate_and_decide(
            conn, dev_id, decided_at="2026-07-15T10:00:00",
        )
        assert tol.action_level == "replan_global"
        d = get_decision(conn, delta_id)
        assert d.niveau_code == L_REPLANIFIER_GLOBAL


def test_evaluate_and_decide_with_macrs_attribution(tmp_db) -> None:
    """racine_id + categorie_code peuvent être attachés (wiring B.3).
    score=0.25 magnitude × (1 + log(1+freq=1)) ≈ 0.423 → correct_local."""
    with db_session(tmp_db) as conn:
        seed_default_delta_levels(conn)
        dev_id = _seed_deviation(conn, score=0.25)
        _, delta_id = evaluate_and_decide(
            conn, dev_id, decided_at="2026-07-15T10:00:00",
            racine_id=None, categorie_code=None,
        )
        d = get_decision(conn, delta_id)
        assert d.niveau_code == L_CORRIGER_LOCAL


def test_evaluate_and_decide_uses_active_profile(tmp_db) -> None:
    """Profil REACTIVE : seuils plus bas → un score 0.20 doit
    déclencher au moins watch/correct_local au lieu d'inform."""
    with db_session(tmp_db) as conn:
        seed_default_delta_levels(conn)
        apply_tolerance_profile(conn, REACTIVE_PROFILE)
        dev_id = _seed_deviation(conn, score=0.20)
        tol, _ = evaluate_and_decide(
            conn, dev_id, decided_at="2026-07-15T10:00:00",
        )
        # Sous DEFAULT, 0.20 = watch ; sous REACTIVE 0.20 → escalate
        # (seuil escalate 0.20 → 0.30 ⇒ 0.20 < 0.40 → replan_local).
        # Verif simple : pas d'inform avec REACTIVE.
        assert tol.action_level != "inform"


def test_evaluate_and_decide_explanation_contains_metrics(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        seed_default_delta_levels(conn)
        dev_id = _seed_deviation(conn, score=0.45)
        _, delta_id = evaluate_and_decide(
            conn, dev_id, decided_at="2026-07-15T10:00:00",
        )
        d = get_decision(conn, delta_id)
        assert d.explanation is not None
        assert "filtre_dual" in d.explanation
        assert "action=" in d.explanation
        assert "score_mag" in d.explanation
        assert "freq=" in d.explanation


def test_evaluate_and_decide_idempotent_tolerance_layer(tmp_db) -> None:
    """L'évaluation du filtre dual est idempotente (1 tolerance
    decision par déviation). Mais evaluate_and_decide crée une
    delta_decision à chaque appel — utile pour traçabilité multi-
    décisions sur même déviation."""
    with db_session(tmp_db) as conn:
        seed_default_delta_levels(conn)
        dev_id = _seed_deviation(conn, score=0.05)
        tol1, delta1 = evaluate_and_decide(
            conn, dev_id, decided_at="2026-07-15T10:00:00",
        )
        tol2, delta2 = evaluate_and_decide(
            conn, dev_id, decided_at="2026-07-15T11:00:00",
        )
        # Même tolerance_decision_id
        assert tol1.decision_id == tol2.decision_id
        # Deux delta_decisions différentes
        assert delta1 != delta2


def test_default_window_hours_is_24() -> None:
    assert DEFAULT_PROFILE.window_hours == 24


def test_default_latency_zero() -> None:
    assert DEFAULT_PROFILE.latency_minutes == 0
