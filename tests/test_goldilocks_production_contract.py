"""Goldilocks #4 — Contrat de Production PC=(T, Ep, Er, C, O) au grain op."""

from __future__ import annotations

import pytest

from pilotage_flux.cybernetic.production_contract import (
    PC_DIMENSIONS,
    PC_ORIGIN_KINDS,
    PCTolerances,
    build_pc_for_operation,
    build_pcs_for_of,
    close_pc_from_op,
    count_pcs_by_status,
    evaluate_pc,
    get_pc,
)
from pilotage_flux.db import db_session


def _seed_op(conn, *, of_op_id=1, of_id="OF-1", ws="WS-1",
             unit_time=2.0, quantity=100.0, hourly_rate=60.0):
    """Helper : seed un workstation + article + OF + opération."""
    conn.execute(
        "INSERT OR IGNORE INTO workstations "
        "(workstation_id, label, sequence_idx) VALUES (?, 'T', 1)",
        (ws,),
    )
    conn.execute(
        "INSERT OR IGNORE INTO articles (article_id, label) "
        "VALUES ('ART', 'T')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO manufacturing_orders "
        "(of_id, article_id, quantity, status) "
        "VALUES (?, 'ART', ?, 'planned')",
        (of_id, quantity),
    )
    conn.execute(
        "INSERT INTO order_operations "
        "(of_op_id, of_id, sequence_idx, workstation_id, unit_time_min, "
        " status) VALUES (?, ?, 1, ?, ?, 'planned')",
        (of_op_id, of_id, ws, unit_time),
    )
    if hourly_rate is not None:
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('workstation', ?, 'hourly_rate', ?)",
            (ws, hourly_rate),
        )


def test_pc_dimensions_and_origin_kinds_constants() -> None:
    assert PC_DIMENSIONS == ("T", "Ep", "Er", "C")
    assert PC_ORIGIN_KINDS == ("sales_order", "candidate", "flux_contract")


def test_default_tolerances() -> None:
    tol = PCTolerances()
    assert tol.time == 0.10
    assert tol.quality == 0.05
    assert tol.quantity == 0.03
    assert tol.cost == 0.10


def test_build_pc_for_operation_computes_targets(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _seed_op(conn, unit_time=2.0, quantity=100.0, hourly_rate=60.0)
        pc_id = build_pc_for_operation(
            conn, 1, origin_kind="sales_order", origin_ref="SO-42",
        )
        row = get_pc(conn, pc_id)
        assert row is not None
        # T = 2 × 100 = 200 min
        assert row["target_time_min"] == 200.0
        # Er = 100 (quantité OF)
        assert row["target_qty_good"] == 100.0
        # Ep = 1.0 par défaut
        assert row["target_quality_rate"] == 1.0
        # C = 200 min × 60 €/h / 60 = 200 €
        assert row["target_cost"] == 200.0
        # O
        assert row["origin_kind"] == "sales_order"
        assert row["origin_ref"] == "SO-42"
        assert row["status"] == "open"


def test_build_pc_uses_custom_tolerances(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _seed_op(conn)
        tol = PCTolerances(
            time=0.20, quality=0.10, quantity=0.05, cost=0.15,
        )
        pc_id = build_pc_for_operation(
            conn, 1, origin_kind="candidate", origin_ref="CAND-1",
            tolerances=tol,
        )
        row = get_pc(conn, pc_id)
        assert row["tolerance_pct_time"] == 0.20
        assert row["tolerance_pct_quality"] == 0.10
        assert row["tolerance_pct_quantity"] == 0.05
        assert row["tolerance_pct_cost"] == 0.15


def test_build_pc_rejects_invalid_origin_kind(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _seed_op(conn)
        with pytest.raises(ValueError, match="origin_kind"):
            build_pc_for_operation(
                conn, 1, origin_kind="foo", origin_ref="X",
            )


def test_build_pc_rejects_unknown_op(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        with pytest.raises(ValueError, match="introuvable"):
            build_pc_for_operation(
                conn, 999, origin_kind="sales_order", origin_ref="X",
            )


def test_build_pcs_for_of_creates_one_per_op(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO workstations (workstation_id, label, sequence_idx) "
            "VALUES ('WS-1', 'T', 1), ('WS-2', 'T', 2)"
        )
        conn.execute(
            "INSERT INTO articles (article_id, label) VALUES ('ART', 'T')"
        )
        conn.execute(
            "INSERT INTO manufacturing_orders "
            "(of_id, article_id, quantity, status) "
            "VALUES ('OF-X', 'ART', 50, 'planned')"
        )
        for i, ws in enumerate(("WS-1", "WS-2"), start=1):
            conn.execute(
                "INSERT INTO order_operations "
                "(of_op_id, of_id, sequence_idx, workstation_id, "
                " unit_time_min, status) "
                "VALUES (?, 'OF-X', ?, ?, 3.0, 'planned')",
                (10 + i, i, ws),
            )
        pcs = build_pcs_for_of(
            conn, "OF-X",
            origin_kind="flux_contract", origin_ref="FX-0001",
        )
        assert len(pcs) == 2


def test_evaluate_pc_open_when_no_actuals(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _seed_op(conn)
        pc_id = build_pc_for_operation(
            conn, 1, origin_kind="sales_order", origin_ref="SO-1",
        )
        ev = evaluate_pc(conn, pc_id)
        assert ev.status == "open"
        assert ev.time_ok is None
        assert ev.quality_ok is None
        assert ev.quantity_ok is None
        assert ev.cost_ok is None
        assert ev.breach_dimensions == ()


def test_close_pc_from_op_fulfilled_in_tolerance(tmp_db) -> None:
    """Acutals dans la bande : status = fulfilled."""
    with db_session(tmp_db) as conn:
        _seed_op(conn, unit_time=2.0, quantity=100.0, hourly_rate=60.0)
        # Actual : 200 min réel pour 200 cible (exactement), qty_good=100
        conn.execute(
            "UPDATE order_operations SET "
            "actual_start = '2026-07-01T08:00:00', "
            "actual_end   = '2026-07-01T11:20:00', "  # 200 min
            "qty_good = 100, qty_scrap = 0 "
            "WHERE of_op_id = 1"
        )
        pc_id = build_pc_for_operation(
            conn, 1, origin_kind="sales_order", origin_ref="SO-1",
        )
        ev = close_pc_from_op(conn, pc_id)
        assert ev.is_fulfilled
        assert ev.time_ok is True
        assert ev.quality_ok is True
        assert ev.quantity_ok is True
        assert ev.cost_ok is True
        assert ev.breach_dimensions == ()


def test_close_pc_from_op_breached_on_time(tmp_db) -> None:
    """Actual 250 min vs cible 200 = +25% → hors bande ±10%."""
    with db_session(tmp_db) as conn:
        _seed_op(conn, unit_time=2.0, quantity=100.0, hourly_rate=60.0)
        conn.execute(
            "UPDATE order_operations SET "
            "actual_start = '2026-07-01T08:00:00', "
            "actual_end   = '2026-07-01T12:10:00', "  # 250 min
            "qty_good = 100, qty_scrap = 0 "
            "WHERE of_op_id = 1"
        )
        pc_id = build_pc_for_operation(
            conn, 1, origin_kind="sales_order", origin_ref="SO-1",
        )
        ev = close_pc_from_op(conn, pc_id)
        assert ev.is_breached
        assert ev.time_ok is False
        # Coût breach aussi (lié au temps × taux horaire)
        assert ev.cost_ok is False
        assert "T" in ev.breach_dimensions
        assert "C" in ev.breach_dimensions
        # Statut + breach_dimensions stockés
        row = get_pc(conn, pc_id)
        assert row["status"] == "breached"
        assert "T" in row["breach_dimensions"]


def test_close_pc_from_op_breached_on_quality(tmp_db) -> None:
    """qty_good=90 / qty_scrap=10 → quality = 0.9 vs cible 1.0,
    écart 10% > tolérance 5%."""
    with db_session(tmp_db) as conn:
        _seed_op(conn, unit_time=2.0, quantity=100.0, hourly_rate=60.0)
        conn.execute(
            "UPDATE order_operations SET "
            "actual_start = '2026-07-01T08:00:00', "
            "actual_end   = '2026-07-01T11:20:00', "
            "qty_good = 90, qty_scrap = 10 "
            "WHERE of_op_id = 1"
        )
        pc_id = build_pc_for_operation(
            conn, 1, origin_kind="sales_order", origin_ref="SO-1",
        )
        ev = close_pc_from_op(conn, pc_id)
        assert ev.is_breached
        assert ev.quality_ok is False
        # Quantité : 90 vs 100 = -10 % > 3 % tolérance → breach Er aussi
        assert ev.quantity_ok is False
        assert "Ep" in ev.breach_dimensions
        assert "Er" in ev.breach_dimensions


def test_close_pc_writes_actuals(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _seed_op(conn, unit_time=2.0, quantity=100.0, hourly_rate=60.0)
        conn.execute(
            "UPDATE order_operations SET "
            "actual_start = '2026-07-01T08:00:00', "
            "actual_end   = '2026-07-01T11:20:00', "
            "qty_good = 100, qty_scrap = 0 "
            "WHERE of_op_id = 1"
        )
        pc_id = build_pc_for_operation(
            conn, 1, origin_kind="sales_order", origin_ref="SO-1",
        )
        close_pc_from_op(conn, pc_id)
        row = get_pc(conn, pc_id)
        assert row["actual_time_min"] == 200.0
        assert row["actual_quality_rate"] == 1.0
        assert row["actual_qty_good"] == 100.0
        assert row["actual_cost"] == 200.0
        assert row["closed_at"] is not None


def test_unique_constraint_on_of_op_id(tmp_db) -> None:
    """Un seul PC par opération (UNIQUE on of_op_id)."""
    import sqlite3 as _sqlite3
    with db_session(tmp_db) as conn:
        _seed_op(conn)
        build_pc_for_operation(
            conn, 1, origin_kind="sales_order", origin_ref="SO-1",
        )
        with pytest.raises(_sqlite3.IntegrityError):
            build_pc_for_operation(
                conn, 1, origin_kind="sales_order", origin_ref="SO-2",
            )


def test_count_pcs_by_status(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        # 3 OFs, 1 op chacun
        conn.execute(
            "INSERT INTO workstations (workstation_id, label, sequence_idx) "
            "VALUES ('WS-1', 'T', 1)"
        )
        conn.execute(
            "INSERT INTO articles (article_id, label) VALUES ('ART', 'T')"
        )
        for i in range(3):
            of_id = f"OF-{i}"
            conn.execute(
                "INSERT INTO manufacturing_orders "
                "(of_id, article_id, quantity, status) "
                "VALUES (?, 'ART', 100, 'planned')", (of_id,),
            )
            conn.execute(
                "INSERT INTO order_operations "
                "(of_op_id, of_id, sequence_idx, workstation_id, "
                " unit_time_min, status) "
                "VALUES (?, ?, 1, 'WS-1', 1.0, 'planned')",
                (100 + i, of_id),
            )
            build_pc_for_operation(
                conn, 100 + i, origin_kind="sales_order",
                origin_ref=f"SO-{i}",
            )
        counts = count_pcs_by_status(conn)
        assert counts == {"open": 3}


def test_pc_cost_zero_when_no_hourly_rate(tmp_db) -> None:
    """Sans hourly_rate paramétré, target_cost = 0 ; pas d'exception."""
    with db_session(tmp_db) as conn:
        _seed_op(conn, hourly_rate=None)  # pas d'insertion paramètre
        pc_id = build_pc_for_operation(
            conn, 1, origin_kind="sales_order", origin_ref="SO-1",
        )
        row = get_pc(conn, pc_id)
        assert row["target_cost"] == 0.0
