"""Tests du scénario stress."""

from __future__ import annotations

import pytest

from pilotage_flux.comparative.scenario import (
    HAZARD_BREAKDOWN,
    HAZARD_LOGISTIC_DELAY,
    HAZARD_PO_DELAY,
    HAZARD_QUALITY_NC,
    HAZARD_URGENT_ORDER,
)
from pilotage_flux.comparative.stress_scenario import (
    SATURATION_TARGETS_STRESS,
    stress_scenario,
    stress_scenario_count_by_kind,
)


def test_saturation_targets_stress_extends_beyond_100() -> None:
    assert SATURATION_TARGETS_STRESS == (
        0.78, 0.86, 0.94, 1.00, 1.05, 1.10,
    )
    # Au moins 1 point au-delà de 100%
    assert any(s > 1.0 for s in SATURATION_TARGETS_STRESS)


def test_stress_horizon_60_days() -> None:
    s = stress_scenario(seed=42)
    assert s.horizon_days == 60


def test_stress_has_12_hazards() -> None:
    s = stress_scenario(seed=42)
    assert len(s.hazards) == 12


def test_stress_hazard_mix_canonical() -> None:
    """Vérifie le mix doctrinal du scénario stress."""
    counts = stress_scenario_count_by_kind()
    assert counts[HAZARD_BREAKDOWN] == 4
    assert counts[HAZARD_QUALITY_NC] == 3
    assert counts[HAZARD_PO_DELAY] == 2
    assert counts[HAZARD_URGENT_ORDER] == 2
    assert counts[HAZARD_LOGISTIC_DELAY] == 1
    assert sum(counts.values()) == 12


def test_stress_seed_jitter_creates_variance() -> None:
    """Deux seeds différents produisent des jours de hazards
    différents."""
    s1 = stress_scenario(seed=42, seed_jitter=True)
    s2 = stress_scenario(seed=123, seed_jitter=True)
    days1 = [h.day for h in s1.hazards]
    days2 = [h.day for h in s2.hazards]
    assert days1 != days2


def test_stress_no_jitter_is_deterministic() -> None:
    """Sans jitter, le scénario est entièrement déterministe."""
    s1 = stress_scenario(seed=42, seed_jitter=False)
    s2 = stress_scenario(seed=123, seed_jitter=False)
    days1 = [h.day for h in s1.hazards]
    days2 = [h.day for h in s2.hazards]
    assert days1 == days2


def test_stress_jitter_preserves_kind_distribution() -> None:
    """Le jitter ne change pas le mix des kinds."""
    s = stress_scenario(seed=42, seed_jitter=True)
    counts: dict[str, int] = {}
    for h in s.hazards:
        counts[h.kind] = counts.get(h.kind, 0) + 1
    assert counts == stress_scenario_count_by_kind()


def test_stress_hazards_within_horizon() -> None:
    """Tous les hazards sont dans la fenêtre [1, horizon-1]."""
    s = stress_scenario(seed=42, horizon_days=60)
    for h in s.hazards:
        assert 1 <= h.day < s.horizon_days


def test_stress_initial_sales_orders_inherited_from_baseline() -> None:
    """Le scénario stress hérite des SOs initiaux du baseline (pour
    permettre la calibration de saturation)."""
    s = stress_scenario(seed=42)
    assert len(s.initial_sales_orders) > 0


def test_stress_payload_jitter_within_bounds() -> None:
    """Le jitter du payload reste dans ±20%."""
    s = stress_scenario(seed=42, seed_jitter=True)
    canonical_qty = {3: 10, 28: 8, 52: 12}  # qty_scrap par jour canonique
    for h in s.hazards:
        if h.kind == HAZARD_QUALITY_NC:
            qty = h.payload.get("qty_scrap")
            assert qty is not None
            assert qty > 0
            # ±20% autour de [8, 10, 12] → globalement dans [6, 15]
            assert 6 <= qty <= 16


def test_stress_custom_name() -> None:
    s = stress_scenario(seed=42, name="custom_stress")
    assert s.name == "custom_stress"


def test_stress_custom_horizon() -> None:
    s = stress_scenario(seed=42, horizon_days=90)
    assert s.horizon_days == 90
    # Tous les hazards restent dans la fenêtre
    for h in s.hazards:
        assert h.day < 90
