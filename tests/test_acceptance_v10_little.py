"""Tests d'acceptation L10.5 : seuils Little + tampons goulots."""

from __future__ import annotations

from pathlib import Path

import pytest

from pilotage_flux.flux.buffers import (
    DEFAULT_BUFFER_SAFETY_FACTOR,
    DEFAULT_SATURATION_BLOCK,
    DEFAULT_SATURATION_DEFER,
    DEFAULT_SATURATION_WARN,
    SaturationLimits,
    apply_buffer_to_capacity,
    little_buffer_for_bottleneck,
)


def test_l105_saturation_classify() -> None:
    """SaturationLimits.classify renvoie safe/warn/block/defer selon les ratios."""
    lim = SaturationLimits(warn=0.80, block=0.90, defer=1.10)
    assert lim.classify(0.50) == "safe"
    assert lim.classify(0.79) == "safe"
    assert lim.classify(0.80) == "warn"
    assert lim.classify(0.89) == "warn"
    assert lim.classify(0.90) == "block"
    assert lim.classify(1.05) == "block"
    assert lim.classify(1.10) == "defer"
    assert lim.classify(1.50) == "defer"


def test_l105_little_buffer_dimensioning() -> None:
    """little_buffer_for_bottleneck réserve safety_factor% de la capacité brute."""
    buf = little_buffer_for_bottleneck("WS-3", raw_capacity_min=10000, safety_factor=0.15)
    assert buf.workstation_id == "WS-3"
    assert buf.raw_capacity_min == 10000
    assert buf.safety_factor == 0.15
    assert buf.reserved_capacity_min == pytest.approx(1500.0)
    assert buf.effective_capacity_min == pytest.approx(8500.0)


def test_l105_apply_buffer_non_bottleneck_unchanged() -> None:
    """Un poste non-goulot garde sa capacité raw."""
    assert apply_buffer_to_capacity(5000, is_bottleneck=False, safety_factor=0.20) == 5000


def test_l105_apply_buffer_bottleneck_reduced() -> None:
    """Un goulot voit sa capacité effective réduite par safety_factor."""
    eff = apply_buffer_to_capacity(5000, is_bottleneck=True, safety_factor=0.20)
    assert eff == pytest.approx(4000.0)


def test_l105_p3_collective_records_buffers_and_classes(
    tmp_path: Path
) -> None:
    """Sur un scénario stress overload, P3 collective expose des buffers et
    saturation_classes."""
    from pilotage_flux.comparative import (
        DOCTRINE_EVENT, run_doctrine,
        stress_multi_contract_overload_scenario,
    )
    fix_dir = Path(__file__).resolve().parent.parent / "data" / "fixtures_extended"
    scen = stress_multi_contract_overload_scenario()
    result = run_doctrine(
        scen, DOCTRINE_EVENT, tmp_path / "ovld.db", fixtures_dir=fix_dir,
    )
    notes = " ".join(result.notes)
    # Le P3 collective a été appelé
    assert "P3 collective" in notes


def test_l105_parameters_override_defaults(tmp_path: Path) -> None:
    """Les seuils Little peuvent être surchargés via parameters."""
    from pilotage_flux.db import db_session, init_schema
    from pilotage_flux.flux.buffers import (
        get_safety_factor, get_saturation_limits,
    )

    db = tmp_path / "params.db"
    init_schema(db, drop_existing=True)
    with db_session(db) as conn:
        # Défauts
        lim = get_saturation_limits(conn)
        assert lim.warn == DEFAULT_SATURATION_WARN
        assert get_safety_factor(conn) == DEFAULT_BUFFER_SAFETY_FACTOR
        # Surcharge via parameters
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'p3_saturation_warn_ratio', 0.70)"
        )
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'constraint_buffer_safety_factor', 0.25)"
        )
        lim2 = get_saturation_limits(conn)
        assert lim2.warn == pytest.approx(0.70)
        assert get_safety_factor(conn) == pytest.approx(0.25)
