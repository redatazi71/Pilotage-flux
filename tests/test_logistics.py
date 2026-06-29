"""Tests de la logistique V2."""

from pathlib import Path

import pytest

from pilotage_flux.aps import compute_candidates
from pilotage_flux.db import db_session
from pilotage_flux.gates import run_p1_promotion
from pilotage_flux.importers import import_referentials
from pilotage_flux.logistics import (
    create_location,
    evacuate,
    feed_workstation,
    list_events,
    list_locations,
    queue_at,
    receive,
    ship,
    transfer,
)


@pytest.fixture
def db_with_of(tmp_db: Path, fixtures_v1_dir: Path) -> tuple[Path, str]:
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        compute_candidates(conn)
        outcome = run_p1_promotion(conn)
        of_id = outcome.ofs_created[0].of_id
    return tmp_db, of_id


def test_create_location_persists(db_with_of: tuple[Path, str]) -> None:
    db_path, _ = db_with_of
    with db_session(db_path) as conn:
        loc = create_location(
            conn, location_id="WS-1-IN", label="Entrée WS-1",
            kind="ws_in", workstation_id="WS-1", capacity=10,
        )
        all_locs = list_locations(conn)
    assert loc.location_id == "WS-1-IN"
    assert loc.kind == "ws_in"
    assert len(all_locs) == 1


def test_create_location_refuses_unknown_kind(db_with_of: tuple[Path, str]) -> None:
    db_path, _ = db_with_of
    with db_session(db_path) as conn:
        with pytest.raises(ValueError, match="kind inconnu"):
            create_location(
                conn, location_id="X", label="x", kind="ZZZ"
            )


def test_create_location_refuses_unknown_workstation(
    db_with_of: tuple[Path, str]
) -> None:
    db_path, _ = db_with_of
    with db_session(db_path) as conn:
        with pytest.raises(ValueError, match="workstation inconnue"):
            create_location(
                conn, location_id="X", label="x", kind="ws_in",
                workstation_id="WS-INEXISTANT",
            )


def test_transfer_emits_event(db_with_of: tuple[Path, str]) -> None:
    db_path, _ = db_with_of
    with db_session(db_path) as conn:
        create_location(conn, location_id="STOCK", label="s", kind="stock")
        create_location(conn, location_id="WS-1-IN", label="i", kind="ws_in",
                        workstation_id="WS-1")
        e = transfer(
            conn, article_id="COMP-X", qty=50,
            from_location="STOCK", to_location="WS-1-IN",
        )
    assert e.event_type == "transfer"
    assert e.qty == 50


def test_feed_workstation_event(db_with_of: tuple[Path, str]) -> None:
    db_path, of_id = db_with_of
    with db_session(db_path) as conn:
        create_location(conn, location_id="WS-1-IN", label="i", kind="ws_in",
                        workstation_id="WS-1")
        e = feed_workstation(
            conn, of_id=of_id, of_op_id=None,
            article_id="COMP-X", qty=100, to_location="WS-1-IN",
        )
    assert e.event_type == "feed"
    assert e.of_id == of_id


def test_evacuate_event(db_with_of: tuple[Path, str]) -> None:
    db_path, of_id = db_with_of
    with db_session(db_path) as conn:
        create_location(conn, location_id="WS-1-OUT", label="o", kind="ws_out",
                        workstation_id="WS-1")
        e = evacuate(
            conn, of_id=of_id, article_id="SEMI-1", qty=95,
            from_location="WS-1-OUT",
        )
    assert e.event_type == "evacuate"
    assert e.qty == 95


def test_ship_event(db_with_of: tuple[Path, str]) -> None:
    db_path, of_id = db_with_of
    with db_session(db_path) as conn:
        create_location(conn, location_id="SHIP", label="exp", kind="shipping")
        e = ship(
            conn, of_id=of_id, article_id="ART-A", qty=50,
            from_location="SHIP",
        )
    assert e.event_type == "ship"


def test_queue_at_computes_net(db_with_of: tuple[Path, str]) -> None:
    """File a un emplacement = arrives - departs."""
    db_path, of_id = db_with_of
    with db_session(db_path) as conn:
        create_location(conn, location_id="WS-1-IN", label="i", kind="ws_in",
                        workstation_id="WS-1")
        create_location(conn, location_id="WS-1-OUT", label="o", kind="ws_out",
                        workstation_id="WS-1")
        # 100 entrent, 60 sortent
        feed_workstation(conn, of_id=of_id, of_op_id=None,
                         article_id="COMP-X", qty=100, to_location="WS-1-IN")
        transfer(conn, article_id="COMP-X", qty=60,
                 from_location="WS-1-IN", to_location="WS-1-OUT")
        net = queue_at(conn, "WS-1-IN")
    assert net == 40  # 100 - 60


def test_receive_event(db_with_of: tuple[Path, str]) -> None:
    db_path, _ = db_with_of
    with db_session(db_path) as conn:
        create_location(conn, location_id="STOCK", label="s", kind="stock")
        e = receive(
            conn, article_id="COMP-X", qty=200, to_location="STOCK",
        )
    assert e.event_type == "receive"


def test_list_events_filters(db_with_of: tuple[Path, str]) -> None:
    db_path, of_id = db_with_of
    with db_session(db_path) as conn:
        create_location(conn, location_id="WS-1-IN", label="i", kind="ws_in",
                        workstation_id="WS-1")
        create_location(conn, location_id="WS-1-OUT", label="o", kind="ws_out",
                        workstation_id="WS-1")
        feed_workstation(conn, of_id=of_id, of_op_id=None,
                         article_id="COMP-X", qty=100, to_location="WS-1-IN")
        evacuate(conn, of_id=of_id, article_id="SEMI-1", qty=95,
                 from_location="WS-1-OUT")
        feeds = list_events(conn, event_type="feed")
        of_events = list_events(conn, of_id=of_id)
    assert len(feeds) == 1
    assert len(of_events) == 2
