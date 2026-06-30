"""Tests du scénario pair stress."""

from __future__ import annotations

from collections import Counter

import pytest

from pilotage_flux.comparative.domain_pair_stress import (
    DOMAIN_TO_HAZARD,
    DOMAINS,
    all_pairs,
    pair_stress_scenario,
)
from pilotage_flux.comparative.scenario import (
    HAZARD_BREAKDOWN,
    HAZARD_LOGISTIC_DELAY,
    HAZARD_PO_DELAY,
    HAZARD_QUALITY_NC,
    HAZARD_URGENT_ORDER,
)


def test_five_domains_canonical() -> None:
    assert DOMAINS == ("demande", "approvisionnement", "logistique",
                        "production", "qualite")


def test_mapping_doctrinal() -> None:
    assert DOMAIN_TO_HAZARD["demande"] == HAZARD_URGENT_ORDER
    assert DOMAIN_TO_HAZARD["approvisionnement"] == HAZARD_PO_DELAY
    assert DOMAIN_TO_HAZARD["logistique"] == HAZARD_LOGISTIC_DELAY
    assert DOMAIN_TO_HAZARD["production"] == HAZARD_BREAKDOWN
    assert DOMAIN_TO_HAZARD["qualite"] == HAZARD_QUALITY_NC


def test_all_pairs_25() -> None:
    pairs = all_pairs()
    assert len(pairs) == 25
    # Inclut la diagonale
    diag = [p for p in pairs if p[0] == p[1]]
    assert len(diag) == 5


def test_pair_scenario_has_12_hazards_by_default() -> None:
    s = pair_stress_scenario("qualite", "logistique", seed=42)
    assert len(s.hazards) == 12


def test_pair_scenario_mix_6_each() -> None:
    """Pour 2 domaines différents : 6 hazards de chaque kind."""
    s = pair_stress_scenario("qualite", "logistique", seed=42)
    counter = Counter(h.kind for h in s.hazards)
    assert counter[HAZARD_QUALITY_NC] == 6
    assert counter[HAZARD_LOGISTIC_DELAY] == 6


def test_pair_scenario_same_domain_12_of_same() -> None:
    """Diagonale (D, D) : 12 hazards du même kind."""
    s = pair_stress_scenario("production", "production", seed=42)
    counter = Counter(h.kind for h in s.hazards)
    assert counter[HAZARD_BREAKDOWN] == 12


def test_pair_scenario_horizon_60_days() -> None:
    s = pair_stress_scenario("qualite", "logistique", seed=42)
    assert s.horizon_days == 60
    for h in s.hazards:
        assert 1 <= h.day < 60


def test_pair_scenario_seeds_produce_variance() -> None:
    s1 = pair_stress_scenario("qualite", "logistique", seed=1)
    s2 = pair_stress_scenario("qualite", "logistique", seed=99)
    days1 = [h.day for h in s1.hazards]
    days2 = [h.day for h in s2.hazards]
    assert days1 != days2


def test_pair_scenario_rejects_unknown_domain() -> None:
    with pytest.raises(ValueError, match="domain_a inconnu"):
        pair_stress_scenario("foo", "qualite", seed=1)
    with pytest.raises(ValueError, match="domain_b inconnu"):
        pair_stress_scenario("qualite", "bar", seed=1)


def test_pair_scenario_payload_has_expected_keys() -> None:
    """Vérifie que les payloads sont bien formés pour les 5 kinds."""
    for d in DOMAINS:
        s = pair_stress_scenario(d, d, seed=42)
        h = s.hazards[0]
        if h.kind == HAZARD_BREAKDOWN:
            assert "workstation_id" in h.payload
            assert "duration_days" in h.payload
        elif h.kind == HAZARD_QUALITY_NC:
            assert "qty_scrap" in h.payload
            assert h.payload["qty_scrap"] >= 6
        elif h.kind == HAZARD_PO_DELAY:
            assert "po_id" in h.payload
            assert "delay_days" in h.payload
        elif h.kind == HAZARD_URGENT_ORDER:
            assert "sales_order_id" in h.payload
            assert "due_day" in h.payload
        elif h.kind == HAZARD_LOGISTIC_DELAY:
            assert "workstation_id" in h.payload
            assert "block_days" in h.payload


def test_pair_scenario_days_spaced() -> None:
    """Au moins quelques hazards sont espacés (pas tous le même jour)."""
    s = pair_stress_scenario("qualite", "production", seed=42)
    days = [h.day for h in s.hazards]
    assert len(set(days)) >= 8   # au moins 8 jours distincts sur 12
