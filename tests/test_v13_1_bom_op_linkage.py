"""V13.1 — BOM-op linkage tests."""

from __future__ import annotations

import sqlite3

from pilotage_flux.comparative.runner import (
    _components_needed_at_or_before_op,
    _get_bom_op_linkage_flag,
    _seed_bom_op_consumption_from_routing,
)
from pilotage_flux.db import db_session


def test_flag_default_off(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        assert _get_bom_op_linkage_flag(conn) is False


def test_flag_on(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'bom_op_linkage_aware', 1.0)"
        )
        assert _get_bom_op_linkage_flag(conn) is True


def test_seed_dispatches_components_across_ops(tmp_db) -> None:
    """3 composants + 3 ops → 1 par op."""
    with db_session(tmp_db) as conn:
        for a in ("PARENT", "COMP-A", "COMP-B", "COMP-C"):
            conn.execute(
                "INSERT INTO articles (article_id, label) VALUES (?, 'T')",
                (a,),
            )
        # 3 ops
        conn.execute(
            "INSERT INTO workstations (workstation_id, label, sequence_idx) "
            "VALUES ('WS-1', 'T', 1)"
        )
        for s in (1, 2, 3):
            conn.execute(
                "INSERT INTO routing_operations "
                "(article_id, sequence_idx, workstation_id, unit_time_min) "
                "VALUES (?, ?, 'WS-1', 1.0)",
                ("PARENT", s),
            )
        # 3 bom_lines
        for child in ("COMP-A", "COMP-B", "COMP-C"):
            conn.execute(
                "INSERT INTO bom_lines (parent_article, child_article, quantity) "
                "VALUES ('PARENT', ?, 1)",
                (child,),
            )
        n = _seed_bom_op_consumption_from_routing(conn)
        assert n == 3
        # Vérifier que les ops 1,2,3 ont chacune un composant
        rows = conn.execute(
            "SELECT child_article, consuming_operation_idx "
            "FROM bom_lines WHERE parent_article = 'PARENT' "
            "ORDER BY consuming_operation_idx"
        ).fetchall()
        ops_used = [r["consuming_operation_idx"] for r in rows]
        assert ops_used == [1, 2, 3]


def test_seed_more_components_than_ops_clamps_to_last_op(tmp_db) -> None:
    """4 composants + 2 ops : derniers composants à op 2 (clamp)."""
    with db_session(tmp_db) as conn:
        for a in ("P", "C1", "C2", "C3", "C4"):
            conn.execute(
                "INSERT INTO articles (article_id, label) VALUES (?, 'T')",
                (a,),
            )
        conn.execute(
            "INSERT INTO workstations (workstation_id, label, sequence_idx) "
            "VALUES ('WS-1', 'T', 1)"
        )
        for s in (1, 2):
            conn.execute(
                "INSERT INTO routing_operations "
                "(article_id, sequence_idx, workstation_id, unit_time_min) "
                "VALUES ('P', ?, 'WS-1', 1.0)",
                (s,),
            )
        for c in ("C1", "C2", "C3", "C4"):
            conn.execute(
                "INSERT INTO bom_lines (parent_article, child_article, quantity) "
                "VALUES ('P', ?, 1)",
                (c,),
            )
        _seed_bom_op_consumption_from_routing(conn)
        rows = conn.execute(
            "SELECT child_article, consuming_operation_idx FROM bom_lines "
            "WHERE parent_article = 'P' ORDER BY child_article"
        ).fetchall()
        # 4 composants, 2 ops : C1→1, C2→2, C3→2, C4→2
        m = {r["child_article"]: r["consuming_operation_idx"] for r in rows}
        assert m["C1"] == 1
        assert m["C2"] == 2
        assert m["C3"] == 2
        assert m["C4"] == 2


def test_seed_is_idempotent(tmp_db) -> None:
    """Re-call ne touche pas les lignes déjà assignées."""
    with db_session(tmp_db) as conn:
        for a in ("P", "C"):
            conn.execute(
                "INSERT INTO articles (article_id, label) VALUES (?, 'T')",
                (a,),
            )
        conn.execute(
            "INSERT INTO workstations (workstation_id, label, sequence_idx) "
            "VALUES ('WS-1', 'T', 1)"
        )
        conn.execute(
            "INSERT INTO routing_operations "
            "(article_id, sequence_idx, workstation_id, unit_time_min) "
            "VALUES ('P', 1, 'WS-1', 1.0)"
        )
        conn.execute(
            "INSERT INTO bom_lines "
            "(parent_article, child_article, quantity, consuming_operation_idx) "
            "VALUES ('P', 'C', 1, 7)"
        )
        n = _seed_bom_op_consumption_from_routing(conn)
        assert n == 0  # déjà assignée
        r = conn.execute(
            "SELECT consuming_operation_idx FROM bom_lines "
            "WHERE parent_article = 'P'"
        ).fetchone()
        assert r["consuming_operation_idx"] == 7  # inchangé


def test_components_needed_at_or_before_op(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        for a in ("P", "C1", "C2", "C3"):
            conn.execute(
                "INSERT INTO articles (article_id, label) VALUES (?, 'T')",
                (a,),
            )
        for c, op in (("C1", 1), ("C2", 2), ("C3", 3)):
            conn.execute(
                "INSERT INTO bom_lines (parent_article, child_article, "
                "quantity, consuming_operation_idx) "
                "VALUES ('P', ?, 1, ?)",
                (c, op),
            )
        assert _components_needed_at_or_before_op(conn, "P", 1) == ["C1"]
        s2 = set(_components_needed_at_or_before_op(conn, "P", 2))
        assert s2 == {"C1", "C2"}
        s3 = set(_components_needed_at_or_before_op(conn, "P", 3))
        assert s3 == {"C1", "C2", "C3"}


def test_components_needed_legacy_null_implies_op_1(tmp_db) -> None:
    """consuming_operation_idx NULL → considéré comme op 1."""
    with db_session(tmp_db) as conn:
        for a in ("P", "C"):
            conn.execute(
                "INSERT INTO articles (article_id, label) VALUES (?, 'T')",
                (a,),
            )
        conn.execute(
            "INSERT INTO bom_lines (parent_article, child_article, quantity) "
            "VALUES ('P', 'C', 1)"
        )
        assert _components_needed_at_or_before_op(conn, "P", 1) == ["C"]
        assert _components_needed_at_or_before_op(conn, "P", 5) == ["C"]
