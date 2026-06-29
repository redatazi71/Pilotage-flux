"""Tests des cycles territoriaux."""

from pathlib import Path

import pytest

from pilotage_flux.db import db_session
from pilotage_flux.importers import import_referentials
from pilotage_flux.zones import (
    close_cycle,
    create_cycle,
    current_open_cycle,
    list_cycles,
    open_cycle,
)
from pilotage_flux.zones.cycles import DEFAULT_CADENCE_DAYS


def test_create_p2_cycle_uses_default_monthly_cadence(tmp_db: Path) -> None:
    with db_session(tmp_db) as conn:
        c = create_cycle(
            conn,
            gate="P2",
            cycle_id="P2-2026-07",
            period_start="2026-07-01",
            period_end="2026-07-31",
        )
    assert c.cadence_days == DEFAULT_CADENCE_DAYS["P2"] == 30
    assert c.status == "planned"
    assert c.opened_at is None


def test_create_p3_cycle_uses_default_weekly_cadence(tmp_db: Path) -> None:
    with db_session(tmp_db) as conn:
        c = create_cycle(
            conn,
            gate="P3",
            cycle_id="P3-2026-W27",
            period_start="2026-07-06",
            period_end="2026-07-12",
        )
    assert c.cadence_days == DEFAULT_CADENCE_DAYS["P3"] == 7


def test_cadence_is_data_driven_from_parameters(
    tmp_db: Path, fixtures_v1_dir: Path
) -> None:
    """Insert un paramètre custom pour P2 et vérifier qu'il prime sur le défaut."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        conn.execute(
            """
            INSERT INTO parameters (scope, scope_ref, name, value_num)
            VALUES ('global', NULL, 'gate_p2_cadence_days', 14)
            """
        )
        c = create_cycle(
            conn,
            gate="P2",
            cycle_id="P2-CUSTOM",
            period_start="2026-08-01",
            period_end="2026-08-14",
        )
    # Le paramètre custom (14 jours) prime sur le défaut (30 jours)
    assert c.cadence_days == 14


def test_invalid_gate_is_rejected(tmp_db: Path) -> None:
    with db_session(tmp_db) as conn:
        with pytest.raises(ValueError, match="Porte inconnue"):
            create_cycle(
                conn,
                gate="P1",
                cycle_id="X",
                period_start="2026-01-01",
                period_end="2026-01-31",
            )


def test_duplicate_cycle_id_is_rejected(tmp_db: Path) -> None:
    with db_session(tmp_db) as conn:
        create_cycle(
            conn,
            gate="P2",
            cycle_id="DUP",
            period_start="2026-01-01",
            period_end="2026-01-31",
        )
        with pytest.raises(ValueError, match="existe déjà"):
            create_cycle(
                conn,
                gate="P2",
                cycle_id="DUP",
                period_start="2026-02-01",
                period_end="2026-02-28",
            )


def test_open_cycle_transitions_planned_to_open(tmp_db: Path) -> None:
    with db_session(tmp_db) as conn:
        create_cycle(
            conn,
            gate="P2",
            cycle_id="P2-X",
            period_start="2026-01-01",
            period_end="2026-01-31",
        )
        c = open_cycle(conn, "P2-X")
    assert c.status == "open"
    assert c.opened_at is not None


def test_open_already_open_cycle_raises(tmp_db: Path) -> None:
    with db_session(tmp_db) as conn:
        create_cycle(
            conn, gate="P2", cycle_id="C", period_start="2026-01-01", period_end="2026-01-31"
        )
        open_cycle(conn, "C")
        with pytest.raises(ValueError, match="attendu 'planned'"):
            open_cycle(conn, "C")


def test_close_cycle_transitions_open_to_closed(tmp_db: Path) -> None:
    with db_session(tmp_db) as conn:
        create_cycle(
            conn, gate="P3", cycle_id="C", period_start="2026-01-01", period_end="2026-01-07"
        )
        open_cycle(conn, "C")
        c = close_cycle(conn, "C")
    assert c.status == "closed"
    assert c.closed_at is not None


def test_close_planned_cycle_raises(tmp_db: Path) -> None:
    with db_session(tmp_db) as conn:
        create_cycle(
            conn, gate="P3", cycle_id="C", period_start="2026-01-01", period_end="2026-01-07"
        )
        with pytest.raises(ValueError, match="attendu 'open'"):
            close_cycle(conn, "C")


def test_current_open_cycle_returns_latest_open(tmp_db: Path) -> None:
    with db_session(tmp_db) as conn:
        create_cycle(
            conn, gate="P2", cycle_id="A", period_start="2026-01-01", period_end="2026-01-31"
        )
        create_cycle(
            conn, gate="P2", cycle_id="B", period_start="2026-02-01", period_end="2026-02-28"
        )
        open_cycle(conn, "A")
        open_cycle(conn, "B")
        c = current_open_cycle(conn, "P2")
    assert c is not None
    # B a été ouvert en dernier
    assert c.cycle_id == "B"


def test_current_open_cycle_returns_none_when_nothing_open(tmp_db: Path) -> None:
    with db_session(tmp_db) as conn:
        c = current_open_cycle(conn, "P2")
    assert c is None


def test_list_cycles_filters_by_gate(tmp_db: Path) -> None:
    with db_session(tmp_db) as conn:
        create_cycle(
            conn, gate="P2", cycle_id="P2-A", period_start="2026-01-01", period_end="2026-01-31"
        )
        create_cycle(
            conn, gate="P3", cycle_id="P3-A", period_start="2026-01-01", period_end="2026-01-07"
        )
        p2_only = list_cycles(conn, gate="P2")
        p3_only = list_cycles(conn, gate="P3")
    assert len(p2_only) == 1
    assert p2_only[0].cycle_id == "P2-A"
    assert len(p3_only) == 1
    assert p3_only[0].cycle_id == "P3-A"


def test_list_cycles_filters_by_status(tmp_db: Path) -> None:
    with db_session(tmp_db) as conn:
        create_cycle(
            conn, gate="P2", cycle_id="A", period_start="2026-01-01", period_end="2026-01-31"
        )
        create_cycle(
            conn, gate="P2", cycle_id="B", period_start="2026-02-01", period_end="2026-02-28"
        )
        open_cycle(conn, "A")
        opened = list_cycles(conn, status="open")
        planned = list_cycles(conn, status="planned")
    assert len(opened) == 1
    assert opened[0].cycle_id == "A"
    assert len(planned) == 1
    assert planned[0].cycle_id == "B"
