"""V13.B (item 4) — modèle de rendement composé par poste."""

from __future__ import annotations

from pilotage_flux.comparative.runner import (
    _compute_op_qty_good_scrap,
    _get_yield_compounding_flag,
    _seed_workstation_yields,
)
from pilotage_flux.db import db_session


def test_flag_default_off(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        assert _get_yield_compounding_flag(conn) is False


def test_flag_on(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'yield_compounding_aware', 1.0)"
        )
        assert _get_yield_compounding_flag(conn) is True


def test_seed_yields_idempotent_and_present(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        for i in range(3):
            conn.execute(
                "INSERT INTO workstations (workstation_id, label, sequence_idx) "
                "VALUES (?, 'T', ?)",
                (f"WS-{i}", i),
            )
        n1 = _seed_workstation_yields(conn)
        assert n1 == 3
        # idempotent : 2e appel ne ré-insère rien
        n2 = _seed_workstation_yields(conn)
        assert n2 == 0
        ys = conn.execute(
            "SELECT value_num FROM parameters WHERE name='yield_rate'"
        ).fetchall()
        assert len(ys) == 3
        assert all(0.9 <= float(r["value_num"]) <= 1.0 for r in ys)


def test_seed_does_not_overwrite_existing_yield(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO workstations (workstation_id, label, sequence_idx) "
            "VALUES ('WS-X', 'T', 1)"
        )
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('workstation', 'WS-X', 'yield_rate', 0.50)"
        )
        n = _seed_workstation_yields(conn)
        assert n == 0  # déjà présent
        r = conn.execute(
            "SELECT value_num FROM parameters WHERE scope='workstation' "
            "AND scope_ref='WS-X' AND name='yield_rate'"
        ).fetchone()
        assert float(r["value_num"]) == 0.50  # inchangé


def test_legacy_scrap_flat_5pct(tmp_db) -> None:
    """Flag off : scrap = round(qty × 0.05), non-compounding."""
    with db_session(tmp_db) as conn:
        op = {"sequence_idx": 1, "workstation_id": "WS-1"}
        good, scrap, pend = _compute_op_qty_good_scrap(
            conn, "OF-X", op, of_qty=100.0, pending_scrap=0.0,
            compounding=False,
        )
        assert scrap == 5.0
        assert good == 95.0
        assert pend == 0.0


def test_legacy_scrap_consumes_pending(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        op = {"sequence_idx": 1, "workstation_id": "WS-1"}
        good, scrap, pend = _compute_op_qty_good_scrap(
            conn, "OF-X", op, of_qty=100.0, pending_scrap=10.0,
            compounding=False,
        )
        assert scrap == 15.0  # 5 base + 10 pending
        assert good == 85.0
        assert pend == 0.0  # pending consommé


def test_compounding_applies_ws_yield_to_incoming(tmp_db) -> None:
    """Compounding op 1 : pas d'op précédente → entrant = of_qty,
    bon = round(of_qty × yield_ws)."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO workstations (workstation_id, label, sequence_idx) "
            "VALUES ('WS-Y', 'T', 1)"
        )
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('workstation', 'WS-Y', 'yield_rate', 0.90)"
        )
        # OF + op 1 (status pending — pas d'op précédente done)
        conn.execute(
            "INSERT INTO articles (article_id, label) VALUES ('ART-C', 'T')"
        )
        conn.execute(
            "INSERT INTO manufacturing_orders (of_id, article_id, quantity, status) "
            "VALUES ('OF-C', 'ART-C', 100, 'in_progress')"
        )
        op = {"sequence_idx": 1, "workstation_id": "WS-Y"}
        good, scrap, _ = _compute_op_qty_good_scrap(
            conn, "OF-C", op, of_qty=100.0, pending_scrap=0.0,
            compounding=True,
        )
        assert good == 90.0   # 100 × 0.90
        assert scrap == 10.0


def test_compounding_chains_from_previous_op(tmp_db) -> None:
    """Op 2 consomme la sortie bonne de l'op 1 (compounding)."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO workstations (workstation_id, label, sequence_idx) "
            "VALUES ('WS-Z', 'T', 1)"
        )
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('workstation', 'WS-Z', 'yield_rate', 0.80)"
        )
        conn.execute(
            "INSERT INTO articles (article_id, label) VALUES ('ART-D', 'T')"
        )
        conn.execute(
            "INSERT INTO manufacturing_orders (of_id, article_id, quantity, status) "
            "VALUES ('OF-D', 'ART-D', 100, 'in_progress')"
        )
        # op 1 déjà 'done' avec qty_good=90
        conn.execute(
            "INSERT INTO order_operations "
            "(of_id, sequence_idx, workstation_id, unit_time_min, status, qty_good) "
            "VALUES ('OF-D', 1, 'WS-Z', 1.0, 'done', 90)"
        )
        op2 = {"sequence_idx": 2, "workstation_id": "WS-Z"}
        good, scrap, _ = _compute_op_qty_good_scrap(
            conn, "OF-D", op2, of_qty=100.0, pending_scrap=0.0,
            compounding=True,
        )
        # entrant = 90 (sortie op 1), × 0.80 = 72
        assert good == 72.0
        assert scrap == 18.0
