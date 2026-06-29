"""Tests des portes de franchissement (V0 : P1 et P4)."""

from pathlib import Path

import pytest

from pilotage_flux.db import db_session
from pilotage_flux.gates import run_p1_promotion
from pilotage_flux.importers import import_referentials


@pytest.fixture
def db_ready(tmp_db: Path, fixtures_dir: Path) -> Path:
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_dir)
    return tmp_db


def test_p1_creates_candidates_and_ofs(db_ready: Path) -> None:
    with db_session(db_ready) as conn:
        outcome = run_p1_promotion(conn)

    assert len(outcome.candidates_created) == 2
    assert len(outcome.ofs_created) == 2
    assert {o.article_id for o in outcome.ofs_created} == {"ART-A"}
    assert sum(o.quantity for o in outcome.ofs_created) == 150


def test_p1_is_idempotent(db_ready: Path) -> None:
    """Un second run P1 ne re-cree rien si rien n'a change."""
    with db_session(db_ready) as conn:
        first = run_p1_promotion(conn)
        second = run_p1_promotion(conn)

    assert len(first.ofs_created) == 2
    assert len(second.candidates_created) == 0
    assert len(second.ofs_created) == 0


def test_p1_reports_overload(db_ready: Path) -> None:
    """La porte P1 evalue la capacite et identifie les goulots."""
    with db_session(db_ready) as conn:
        outcome = run_p1_promotion(conn)
    overloaded = [w for w in outcome.workstation_load if w.is_overloaded]
    assert len(overloaded) == 1
    assert overloaded[0].workstation_id == "WS-2"
    assert outcome.has_overload is True


def test_p1_traces_decisions_in_event_store(db_ready: Path) -> None:
    """Toute decision P1 est tracee dans event_store et gate_decisions."""
    with db_session(db_ready) as conn:
        run_p1_promotion(conn)

        of_events = conn.execute(
            "SELECT COUNT(*) AS n FROM event_store WHERE event_type = 'OF_CREATED'"
        ).fetchone()
        p1_decisions = conn.execute(
            "SELECT COUNT(*) AS n FROM gate_decisions WHERE gate = 'P1'"
        ).fetchone()

    assert of_events["n"] == 2
    assert p1_decisions["n"] == 2
