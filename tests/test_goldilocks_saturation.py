"""Goldilocks #1 — calibration saturation R1 par volume de SOs."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from pilotage_flux.comparative.saturation import (
    SATURATION_TARGETS,
    calibrate_scenario_to_saturation,
    compute_saturation,
    identify_bottleneck,
)
from pilotage_flux.comparative.scenario import baseline_scenario
from pilotage_flux.db import db_session


FIXTURES = Path("data/fixtures_extended")


def test_saturation_targets_contains_expected_levels() -> None:
    # 5 niveaux Goldilocks (78-94) + sur-saturation (100) pour test
    # de cohérence interne de la doctrine (érosion attendue à 100%)
    assert SATURATION_TARGETS == (0.78, 0.82, 0.86, 0.90, 0.94, 1.00)


def test_identify_bottleneck_empty_db(tmp_db) -> None:
    """Pas de SOs ni routings → pas de goulot identifié."""
    with db_session(tmp_db) as conn:
        ws, loads = identify_bottleneck(conn)
        assert ws is None
        assert loads == {}


def test_identify_bottleneck_picks_heaviest_ws(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        for w in ("WS-A", "WS-B"):
            conn.execute(
                "INSERT INTO workstations (workstation_id, label, sequence_idx) "
                "VALUES (?, 'T', 1)", (w,),
            )
        conn.execute(
            "INSERT INTO articles (article_id, label) VALUES ('ART', 'T')"
        )
        # WS-A : 5 min/u ; WS-B : 1 min/u
        conn.execute(
            "INSERT INTO routing_operations "
            "(article_id, sequence_idx, workstation_id, unit_time_min) "
            "VALUES ('ART', 1, 'WS-A', 5.0), ('ART', 2, 'WS-B', 1.0)"
        )
        conn.execute(
            "INSERT INTO sales_orders "
            "(sales_order_id, article_id, quantity, due_date) "
            "VALUES ('SO-1', 'ART', 100, '2026-08-01')"
        )
        ws, loads = identify_bottleneck(conn)
        assert ws == "WS-A"
        # 100 × 5 = 500 min sur A ; 100 × 1 = 100 sur B
        assert abs(loads["WS-A"] - 500.0) < 0.1
        assert abs(loads["WS-B"] - 100.0) < 0.1


def test_compute_saturation_formula(tmp_db) -> None:
    """saturation = load / (horizon_days × shift_minutes)."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO workstations (workstation_id, label, sequence_idx) "
            "VALUES ('WS-1', 'T', 1)"
        )
        conn.execute(
            "INSERT INTO articles (article_id, label) VALUES ('ART', 'T')"
        )
        conn.execute(
            "INSERT INTO routing_operations "
            "(article_id, sequence_idx, workstation_id, unit_time_min) "
            "VALUES ('ART', 1, 'WS-1', 10.0)"
        )
        conn.execute(
            "INSERT INTO sales_orders "
            "(sales_order_id, article_id, quantity, due_date) "
            "VALUES ('SO-1', 'ART', 200, '2026-08-01')"
        )
        # load = 200 × 10 = 2000 min ; capacity = 10 × 480 = 4800 ;
        # saturation = 2000 / 4800 ≈ 0.4167
        sat = compute_saturation(conn, horizon_days=10, ws_id="WS-1",
                                 shift_minutes=480)
        assert 0.41 < sat < 0.43


def test_compute_saturation_with_capacity_factor(tmp_db) -> None:
    """capacity_factor < 1 → augmente la charge effective."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO workstations (workstation_id, label, sequence_idx) "
            "VALUES ('WS-1', 'T', 1)"
        )
        conn.execute(
            "INSERT INTO articles (article_id, label) VALUES ('ART', 'T')"
        )
        conn.execute(
            "INSERT INTO routing_operations "
            "(article_id, sequence_idx, workstation_id, unit_time_min) "
            "VALUES ('ART', 1, 'WS-1', 10.0)"
        )
        conn.execute(
            "INSERT INTO sales_orders "
            "(sales_order_id, article_id, quantity, due_date) "
            "VALUES ('SO-1', 'ART', 200, '2026-08-01')"
        )
        # capacity_factor=0.5 → charge effective × 2 = 4000 min sur 4800 = 0.833
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('workstation', 'WS-1', 'capacity_factor', 0.5)"
        )
        sat = compute_saturation(conn, horizon_days=10, ws_id="WS-1",
                                 shift_minutes=480)
        assert 0.82 < sat < 0.84


def test_compute_load_expands_bom(tmp_db) -> None:
    """Demande d'un article fini induit charge sur WS de ses composants."""
    with db_session(tmp_db) as conn:
        for w in ("WS-1", "WS-2"):
            conn.execute(
                "INSERT INTO workstations (workstation_id, label, sequence_idx) "
                "VALUES (?, 'T', 1)", (w,),
            )
        for a in ("ART-P", "SEMI"):
            conn.execute(
                "INSERT INTO articles (article_id, label) VALUES (?, 'T')",
                (a,),
            )
        # ART-P consomme 2× SEMI ; ART-P routing sur WS-1, SEMI sur WS-2
        conn.execute(
            "INSERT INTO bom_lines (parent_article, child_article, quantity) "
            "VALUES ('ART-P', 'SEMI', 2)"
        )
        conn.execute(
            "INSERT INTO routing_operations "
            "(article_id, sequence_idx, workstation_id, unit_time_min) "
            "VALUES ('ART-P', 1, 'WS-1', 3.0), ('SEMI', 1, 'WS-2', 5.0)"
        )
        conn.execute(
            "INSERT INTO sales_orders "
            "(sales_order_id, article_id, quantity, due_date) "
            "VALUES ('SO-1', 'ART-P', 10, '2026-08-01')"
        )
        # WS-1 : 10 × 3 = 30 ; WS-2 : (10 × 2) × 5 = 100
        ws, loads = identify_bottleneck(conn)
        assert ws == "WS-2"
        assert abs(loads["WS-2"] - 100.0) < 0.1
        assert abs(loads["WS-1"] - 30.0) < 0.1


def test_calibrate_scenario_no_sos_returns_unchanged() -> None:
    s = baseline_scenario()
    s = replace(s, initial_sales_orders=[])
    out = calibrate_scenario_to_saturation(
        s, 0.86, fixtures_dir=FIXTURES,
    )
    assert out.initial_sales_orders == []


def test_calibrate_scenario_scales_quantities() -> None:
    """Calibrer à 0.86 doit produire une saturation mesurée proche de 0.86."""
    from pilotage_flux.comparative.saturation import _compute_saturation_for_scenario

    base = baseline_scenario()
    calibrated = calibrate_scenario_to_saturation(
        base, 0.86, fixtures_dir=FIXTURES,
    )
    measured, _ = _compute_saturation_for_scenario(
        calibrated, FIXTURES,
    )
    # Tolérance large : arrondi à l'unité sur les quantités SO
    assert 0.82 < measured < 0.90


@pytest.mark.parametrize("target", SATURATION_TARGETS)
def test_calibrate_to_each_target_is_close(target: float) -> None:
    """Sur chaque cible 0.78..0.94, la saturation mesurée est dans ±5pp."""
    from pilotage_flux.comparative.saturation import _compute_saturation_for_scenario

    base = baseline_scenario()
    calibrated = calibrate_scenario_to_saturation(
        base, target, fixtures_dir=FIXTURES,
    )
    measured, _ = _compute_saturation_for_scenario(
        calibrated, FIXTURES,
    )
    assert abs(measured - target) < 0.05
