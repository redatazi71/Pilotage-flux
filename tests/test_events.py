"""Test de l'event store."""

from pathlib import Path

from pilotage_flux.db import db_session
from pilotage_flux.events import (
    EventType,
    append_event,
    fetch_events,
    fetch_events_for,
)
from pilotage_flux.events.event_store import parse_payload


def test_append_and_fetch(tmp_db: Path) -> None:
    with db_session(tmp_db) as conn:
        eid = append_event(
            conn,
            aggregate_type="manufacturing_order",
            aggregate_id="OF-001",
            event_type=EventType.OF_CREATED,
            payload={"qty": 100, "article": "ART-A"},
            actor="test",
            source_module="test_events",
        )
        assert eid >= 1

        events = fetch_events(conn)
    assert len(events) == 1
    e = events[0]
    assert e["event_type"] == "OF_CREATED"
    assert parse_payload(e) == {"qty": 100, "article": "ART-A"}


def test_chronological_order(tmp_db: Path) -> None:
    with db_session(tmp_db) as conn:
        ids = []
        for ev_type in [
            EventType.OF_CREATED,
            EventType.OF_LAUNCHED,
            EventType.OP_STARTED,
            EventType.OP_FINISHED,
            EventType.OF_CLOSED,
        ]:
            ids.append(
                append_event(
                    conn,
                    aggregate_type="manufacturing_order",
                    aggregate_id="OF-001",
                    event_type=ev_type,
                )
            )
        events = fetch_events_for(conn, "manufacturing_order", "OF-001")
    assert [e["event_id"] for e in events] == ids
    assert [e["event_type"] for e in events] == [
        "OF_CREATED",
        "OF_LAUNCHED",
        "OP_STARTED",
        "OP_FINISHED",
        "OF_CLOSED",
    ]


def test_fetch_for_isolates_aggregates(tmp_db: Path) -> None:
    with db_session(tmp_db) as conn:
        append_event(
            conn,
            aggregate_type="manufacturing_order",
            aggregate_id="OF-A",
            event_type=EventType.OF_CREATED,
        )
        append_event(
            conn,
            aggregate_type="manufacturing_order",
            aggregate_id="OF-B",
            event_type=EventType.OF_CREATED,
        )
        events_a = fetch_events_for(conn, "manufacturing_order", "OF-A")
        events_b = fetch_events_for(conn, "manufacturing_order", "OF-B")
    assert len(events_a) == 1
    assert len(events_b) == 1
    assert events_a[0]["aggregate_id"] == "OF-A"
    assert events_b[0]["aggregate_id"] == "OF-B"
