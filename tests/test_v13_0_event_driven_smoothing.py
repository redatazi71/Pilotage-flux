"""V13.0 — Event-driven smoothing reactivity tests.

Vérifie que les helpers de pull-forward modifient bien
`state.scheduled_launch_day` pour les OFs encore 'created' impactés
par un corrective action.
"""

from __future__ import annotations

import sqlite3

from pilotage_flux.comparative.runner import (
    _get_event_driven_smoothing_advance_days,
    _pull_forward_all_pending_ofs,
    _pull_forward_pending_ofs_by_parent_article,
    _pull_forward_pending_ofs_by_ws,
)
from pilotage_flux.db import db_session


class _FakeState:
    def __init__(self, sched: dict[str, int]) -> None:
        self.scheduled_launch_day = sched


def test_advance_days_default_zero(tmp_db) -> None:
    """V13.0 OFF par défaut (rétrocompat EVENT V11)."""
    with db_session(tmp_db) as conn:
        assert _get_event_driven_smoothing_advance_days(conn) == 0


def test_advance_days_param_read(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'event_driven_smoothing_advance_days', 5.0)"
        )
        assert _get_event_driven_smoothing_advance_days(conn) == 5


def test_pull_forward_by_ws_advances_only_pending(tmp_db) -> None:
    """OFs 'created' routant par WS-A avancés ; OFs 'launched' ignorés."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO workstations (workstation_id, label, sequence_idx) "
            "VALUES ('WS-A', 'T', 1)"
        )
        conn.execute(
            "INSERT INTO articles (article_id, label) VALUES ('ART-X', 'T')"
        )
        conn.execute(
            "INSERT INTO routing_operations "
            "(article_id, sequence_idx, workstation_id, unit_time_min) "
            "VALUES ('ART-X', 1, 'WS-A', 5.0)"
        )
        # OF1 created, OF2 launched
        for oid, status in (("OF1", "created"), ("OF2", "launched")):
            conn.execute(
                "INSERT INTO manufacturing_orders "
                "(of_id, article_id, quantity, status) "
                "VALUES (?, 'ART-X', 10, ?)",
                (oid, status),
            )
        state = _FakeState({"OF1": 10, "OF2": 5})
        affected = _pull_forward_pending_ofs_by_ws(
            conn, state, "WS-A", day_current=2, days_advance=3,
        )
        assert "OF1" in affected
        assert "OF2" not in affected
        assert state.scheduled_launch_day["OF1"] == 7  # 10 - 3
        assert state.scheduled_launch_day["OF2"] == 5  # unchanged


def test_pull_forward_clamps_to_day_current_plus_one(tmp_db) -> None:
    """L'avancement ne descend jamais en-dessous de day_current + 1."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO workstations (workstation_id, label, sequence_idx) "
            "VALUES ('WS-A', 'T', 1)"
        )
        conn.execute(
            "INSERT INTO articles (article_id, label) VALUES ('ART-X', 'T')"
        )
        conn.execute(
            "INSERT INTO routing_operations "
            "(article_id, sequence_idx, workstation_id, unit_time_min) "
            "VALUES ('ART-X', 1, 'WS-A', 5.0)"
        )
        conn.execute(
            "INSERT INTO manufacturing_orders "
            "(of_id, article_id, quantity, status) "
            "VALUES ('OF1', 'ART-X', 10, 'created')"
        )
        state = _FakeState({"OF1": 4})
        _pull_forward_pending_ofs_by_ws(
            conn, state, "WS-A", day_current=5, days_advance=10,
        )
        # 4 - 10 = -6, clampé à max(5+1, -6) = 6 ; mais 6 > 4 donc no change
        assert state.scheduled_launch_day["OF1"] == 4


def test_pull_forward_by_parent_article(tmp_db) -> None:
    """OFs dont l'article est PARENT BOM de child_article sont avancés."""
    with db_session(tmp_db) as conn:
        for a in ("ART-P", "ART-C"):
            conn.execute(
                "INSERT INTO articles (article_id, label) VALUES (?, 'T')",
                (a,),
            )
        conn.execute(
            "INSERT INTO bom_lines (parent_article, child_article, quantity) "
            "VALUES ('ART-P', 'ART-C', 1)"
        )
        conn.execute(
            "INSERT INTO manufacturing_orders "
            "(of_id, article_id, quantity, status) "
            "VALUES ('OF-P', 'ART-P', 5, 'created')"
        )
        state = _FakeState({"OF-P": 10})
        affected = _pull_forward_pending_ofs_by_parent_article(
            conn, state, "ART-C", day_current=1, days_advance=3,
        )
        assert "OF-P" in affected
        assert state.scheduled_launch_day["OF-P"] == 7


def test_pull_forward_all_pending(tmp_db) -> None:
    """Tous les OFs 'created' avancés (effet qc_intervention)."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO articles (article_id, label) VALUES ('ART-Y', 'T')"
        )
        for oid in ("OF1", "OF2"):
            conn.execute(
                "INSERT INTO manufacturing_orders "
                "(of_id, article_id, quantity, status) "
                "VALUES (?, 'ART-Y', 10, 'created')",
                (oid,),
            )
        state = _FakeState({"OF1": 8, "OF2": 15})
        affected = _pull_forward_all_pending_ofs(
            conn, state, day_current=2, days_advance=4,
        )
        assert set(affected) == {"OF1", "OF2"}
        assert state.scheduled_launch_day["OF1"] == 4
        assert state.scheduled_launch_day["OF2"] == 11


def test_pull_forward_zero_days_noop(tmp_db) -> None:
    """advance_days=0 → aucune modification (V13.0 désactivé)."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO workstations (workstation_id, label, sequence_idx) "
            "VALUES ('WS-Z', 'T', 1)"
        )
        conn.execute(
            "INSERT INTO articles (article_id, label) VALUES ('ART-Z', 'T')"
        )
        conn.execute(
            "INSERT INTO routing_operations "
            "(article_id, sequence_idx, workstation_id, unit_time_min) "
            "VALUES ('ART-Z', 1, 'WS-Z', 5.0)"
        )
        conn.execute(
            "INSERT INTO manufacturing_orders "
            "(of_id, article_id, quantity, status) "
            "VALUES ('OF-Z', 'ART-Z', 10, 'created')"
        )
        state = _FakeState({"OF-Z": 10})
        affected = _pull_forward_pending_ofs_by_ws(
            conn, state, "WS-Z", day_current=1, days_advance=0,
        )
        assert affected == []
        assert state.scheduled_launch_day["OF-Z"] == 10
