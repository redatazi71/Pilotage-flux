"""Étape 2 — KPIs robustesse + agilité du banc cybernétique."""

from __future__ import annotations

import pytest

from pilotage_flux.comparative.bce_kpis_advanced import (
    AgiliteResult,
    RobustesseResult,
    compute_agilite,
    compute_robustesse,
)


# ---------------------------------------------------------------------
# Robustesse
# ---------------------------------------------------------------------

def test_robustesse_breaking_point_interpolated() -> None:
    """KPI {0.78:1.0, 0.86:0.95, 0.94:0.85}, threshold 0.90 →
    breaking_point ∈ (0.86, 0.94) interpolé."""
    r = compute_robustesse(
        {0.78: 1.0, 0.86: 0.95, 0.94: 0.85},
        kpi_threshold=0.90,
    )
    assert r.breaking_point_saturation is not None
    # 0.86 + (0.94-0.86) × (0.95-0.90)/(0.95-0.85) = 0.86 + 0.04 = 0.90
    assert abs(r.breaking_point_saturation - 0.90) < 1e-9
    assert r.monotone_decreasing is True
    assert r.kpi_at_max_saturation == 0.85


def test_robustesse_robuste_partout_breaking_point_none() -> None:
    """Tous les KPIs ≥ threshold → pas de rupture détectée."""
    r = compute_robustesse(
        {0.78: 1.0, 0.86: 0.98, 0.94: 0.95},
        kpi_threshold=0.90,
    )
    assert r.breaking_point_saturation is None


def test_robustesse_jamais_robuste_breaking_point_min() -> None:
    """Tous les KPIs < threshold → breaking_point = saturation min."""
    r = compute_robustesse(
        {0.78: 0.80, 0.86: 0.75, 0.94: 0.70},
        kpi_threshold=0.90,
    )
    assert r.breaking_point_saturation == 0.78


def test_robustesse_franchissement_au_premier_pas() -> None:
    """Franchissement direct entre 1er et 2e point."""
    r = compute_robustesse(
        {0.78: 0.95, 0.86: 0.80, 0.94: 0.50},
        kpi_threshold=0.90,
    )
    assert r.breaking_point_saturation is not None
    # 0.78 + (0.86-0.78) × (0.95-0.90)/(0.95-0.80) = 0.78 + 0.0267
    assert abs(r.breaking_point_saturation - 0.80667) < 1e-3


def test_robustesse_kpis_egaux_no_division_by_zero() -> None:
    """Bord : k1==k2 → renvoie s2 directement."""
    r = compute_robustesse(
        {0.78: 1.0, 0.86: 0.90, 0.94: 0.90},
        kpi_threshold=0.90,
    )
    # 0.86 et 0.94 sont tous deux = threshold → pas de franchissement
    # strict (k2 >= threshold). Reste robuste.
    assert r.breaking_point_saturation is None


def test_robustesse_non_monotone_flagged() -> None:
    """Courbe non-monotone : flague mais calcule quand même."""
    r = compute_robustesse(
        {0.78: 0.85, 0.86: 0.95, 0.94: 0.80},
        kpi_threshold=0.90,
    )
    assert r.monotone_decreasing is False


def test_robustesse_empty_raises() -> None:
    with pytest.raises(ValueError, match="vide"):
        compute_robustesse({})


def test_robustesse_single_point_no_crossing() -> None:
    r = compute_robustesse({0.86: 0.95}, kpi_threshold=0.90)
    assert r.breaking_point_saturation is None
    assert r.kpi_at_max_saturation == 0.95


def test_robustesse_single_point_under_threshold() -> None:
    r = compute_robustesse({0.86: 0.80}, kpi_threshold=0.90)
    # Tous (= 1 point) sous le seuil → breaking_point = min
    assert r.breaking_point_saturation == 0.86


# ---------------------------------------------------------------------
# Agilité
# ---------------------------------------------------------------------

def test_agilite_recovery_observed() -> None:
    """Hazard jour 5 sur WIP nominal 10. Le WIP redescend dans la
    bande [9, 11] au jour 8 → recovery_days = 3."""
    daily_wip = {
        1: 10, 2: 10, 3: 10, 4: 10,
        5: 15,        # hazard
        6: 14, 7: 12, 8: 11,   # retour à la bande au jour 8
        9: 10, 10: 10,
    }
    res = compute_agilite(daily_wip, [5])
    assert res.n_hazards == 1
    assert res.n_recoveries_observed == 1
    assert res.recovery_days_per_hazard == [3.0]
    assert res.mean_recovery_days == 3.0


def test_agilite_no_recovery_returns_none() -> None:
    """Le WIP ne retombe pas dans la bande → recovery = None."""
    daily_wip = {
        1: 10, 2: 10, 3: 10, 4: 10,
        5: 20,
        6: 20, 7: 20, 8: 20, 9: 20, 10: 20,
        11: 20, 12: 20, 13: 20, 14: 20, 15: 20,
    }
    res = compute_agilite(daily_wip, [5])
    assert res.recovery_days_per_hazard == [None]
    assert res.mean_recovery_days is None
    assert res.n_recoveries_observed == 0


def test_agilite_multiple_hazards_average() -> None:
    """3 hazards. Vérifie moyenne sur les recoveries observées."""
    daily_wip = {
        # nominal autour de 10
        1: 10, 2: 10, 3: 10,
        4: 12,                # h1 (nominal pré [1..3] = 10, bande [9,11])
        5: 10,                # recovery h1 = 1
        6: 10, 7: 10,
        8: 15,                # h2 (nominal pré [5..7] = 10, bande [9,11])
        9: 14, 10: 13, 11: 11,  # recovery h2 = 3 (11 dans [9,11])
        12: 10,
        13: 15,               # h3 (nominal pré [10..12]=11.33, bande [10.2,12.47])
        14: 14, 15: 12, 16: 10,  # recovery h3 = 2 (15→12 dans bande)
    }
    res = compute_agilite(daily_wip, [4, 8, 13])
    assert res.recovery_days_per_hazard == [1.0, 3.0, 2.0]
    assert res.mean_recovery_days == pytest.approx(2.0, abs=0.01)


def test_agilite_nominal_zero_skips() -> None:
    """Si nominal pré-hazard = 0, on skip ce hazard (pas de bande)."""
    daily_wip = {
        1: 0, 2: 0, 3: 0,
        5: 5,    # hazard sur système froid
    }
    res = compute_agilite(daily_wip, [5])
    assert res.recovery_days_per_hazard == [None]


def test_agilite_empty_daily_wip() -> None:
    res = compute_agilite({}, [3])
    assert res.recovery_days_per_hazard == [None]
    assert res.mean_recovery_days is None
    assert res.n_hazards == 1


def test_agilite_empty_hazards() -> None:
    res = compute_agilite({1: 10, 2: 10}, [])
    assert res.recovery_days_per_hazard == []
    assert res.mean_recovery_days is None
    assert res.n_hazards == 0
    assert res.n_recoveries_observed == 0


def test_agilite_tolerance_band() -> None:
    """Avec tolerance_pct=0.2 (bande ±20%), le WIP 12 sur nominal 10
    est dans [8, 12] → recovery au jour suivant."""
    daily_wip = {
        1: 10, 2: 10, 3: 10,
        5: 20,
        6: 12,
    }
    res = compute_agilite(daily_wip, [5], tolerance_pct=0.20)
    assert res.recovery_days_per_hazard == [1.0]
    # Avec tolerance 0.05 (bande ±5%), 12 n'est plus dans [9.5, 10.5]
    res2 = compute_agilite(daily_wip, [5], tolerance_pct=0.05)
    assert res2.recovery_days_per_hazard == [None]


def test_agilite_window_cap_no_recovery_after() -> None:
    """Si le WIP redescend mais hors fenêtre → None."""
    daily_wip = {
        1: 10, 2: 10, 3: 10,
        5: 20,
        # Hors fenêtre 10 jours (jour 6..15)
        16: 10,
    }
    res = compute_agilite(
        daily_wip, [5], max_recovery_window_days=10,
    )
    assert res.recovery_days_per_hazard == [None]


def test_agilite_pre_hazard_window_short() -> None:
    """Hazard très tôt (jour 1) — utilise les jours dispo."""
    daily_wip = {
        0: 10,
        1: 15,    # hazard
        2: 10,
    }
    res = compute_agilite(
        daily_wip, [1], pre_hazard_window=3,
    )
    # Pré-hazard fallback sur jour 0 → nominal = 10, bande [9, 11]
    # jour 2 wip=10 dans [9, 11] → recovery = 1
    assert res.recovery_days_per_hazard == [1.0]
