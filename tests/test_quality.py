"""Tests qualite V2 : controles + NC + libération."""

from pathlib import Path

import pytest

from pilotage_flux.aps import compute_candidates
from pilotage_flux.db import db_session
from pilotage_flux.gates import run_p1_promotion
from pilotage_flux.importers import import_referentials
from pilotage_flux.quality import (
    block_of,
    create_control,
    declare_control_fail,
    declare_control_pass,
    list_controls,
    list_events,
    open_nc,
    release_of,
    rework_nc,
    scrap_nc,
)


@pytest.fixture
def db_with_of(tmp_db: Path, fixtures_v1_dir: Path) -> tuple[Path, str]:
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        compute_candidates(conn)
        outcome = run_p1_promotion(conn)
        of_id = outcome.ofs_created[0].of_id
    return tmp_db, of_id


def test_create_control_persists(db_with_of: tuple[Path, str]) -> None:
    db_path, _ = db_with_of
    with db_session(db_path) as conn:
        c = create_control(
            conn, article_id="ART-A", label="Visuel piece",
            criterion="visuel_5pct",
        )
        all_c = list_controls(conn, article_id="ART-A")
    assert c.label == "Visuel piece"
    assert c.blocking is True
    assert len(all_c) == 1


def test_create_control_refuses_invalid_sample_rate(
    db_with_of: tuple[Path, str]
) -> None:
    db_path, _ = db_with_of
    with db_session(db_path) as conn:
        with pytest.raises(ValueError, match="sample_rate"):
            create_control(
                conn, article_id="ART-A", label="x", criterion="x", sample_rate=1.5
            )


def test_declare_control_pass_creates_event(db_with_of: tuple[Path, str]) -> None:
    db_path, of_id = db_with_of
    with db_session(db_path) as conn:
        c = create_control(
            conn, article_id="ART-A", label="x", criterion="x"
        )
        e = declare_control_pass(
            conn, of_id=of_id, control_id=c.control_id, qty_concerned=50
        )
    assert e.event_type == "control_pass"
    assert e.qty_concerned == 50


def test_declare_control_fail_creates_event_with_severity(
    db_with_of: tuple[Path, str]
) -> None:
    db_path, of_id = db_with_of
    with db_session(db_path) as conn:
        c = create_control(
            conn, article_id="ART-A", label="x", criterion="x"
        )
        e = declare_control_fail(
            conn, of_id=of_id, control_id=c.control_id,
            qty_concerned=10, severity="critical",
            explanation="non conformite dimensionnelle",
        )
    assert e.event_type == "control_fail"
    assert e.severity == "critical"


def test_nc_workflow_open_rework_scrap_release(
    db_with_of: tuple[Path, str]
) -> None:
    """Cycle complet d'une NC : ouverture -> retouche -> scrap residual -> liberation."""
    db_path, of_id = db_with_of
    with db_session(db_path) as conn:
        open_nc(conn, of_id=of_id, qty_concerned=20, explanation="defaut visuel")
        rework_nc(conn, of_id=of_id, qty_reworked=15)
        scrap_nc(conn, of_id=of_id, qty_scrapped=5)
        release_of(conn, of_id=of_id, explanation="apres retouche OK")
        evs = list_events(conn, of_id=of_id)
    types = [e.event_type for e in evs]
    assert "nc_opened" in types
    assert "nc_rework" in types
    assert "nc_scrap" in types
    assert "release" in types


def test_open_nc_refuses_zero_qty(db_with_of: tuple[Path, str]) -> None:
    db_path, of_id = db_with_of
    with db_session(db_path) as conn:
        with pytest.raises(ValueError, match="strictement positif"):
            open_nc(conn, of_id=of_id, qty_concerned=0)


def test_block_of_records_event(db_with_of: tuple[Path, str]) -> None:
    db_path, of_id = db_with_of
    with db_session(db_path) as conn:
        e = block_of(conn, of_id=of_id, reason="produit en attente expertise")
    assert e.event_type == "block"
    assert e.severity == "critical"


def test_list_events_filters_by_type(db_with_of: tuple[Path, str]) -> None:
    db_path, of_id = db_with_of
    with db_session(db_path) as conn:
        c = create_control(
            conn, article_id="ART-A", label="x", criterion="x"
        )
        declare_control_pass(
            conn, of_id=of_id, control_id=c.control_id, qty_concerned=10
        )
        declare_control_fail(
            conn, of_id=of_id, control_id=c.control_id, qty_concerned=5,
        )
        passed = list_events(conn, of_id=of_id, event_type="control_pass")
        failed = list_events(conn, of_id=of_id, event_type="control_fail")
    assert len(passed) == 1
    assert len(failed) == 1
