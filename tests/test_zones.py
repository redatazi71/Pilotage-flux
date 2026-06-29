"""Tests des zones de planification (libre / negociable / gelee)."""

from pathlib import Path

import pytest

from pilotage_flux.aps import compute_candidates
from pilotage_flux.db import db_session
from pilotage_flux.importers import import_referentials
from pilotage_flux.zones import (
    ZONE_GELEE,
    ZONE_LIBRE,
    ZONE_NEGOCIABLE,
    current_zone,
    fetch_in_zone,
    move_candidate_to_zone,
    transitions_for,
)


@pytest.fixture
def db_with_candidates(tmp_db: Path, fixtures_v1_dir: Path) -> Path:
    """Base avec candidats CBN multi-niveau (tous en zone 'libre')."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        compute_candidates(conn)
    return tmp_db


def test_cbn_creates_candidates_in_zone_libre(db_with_candidates: Path) -> None:
    """Par défaut, les candidates sortent du CBN en zone libre."""
    with db_session(db_with_candidates) as conn:
        rows = conn.execute(
            "SELECT candidate_id, zone FROM candidate_orders"
        ).fetchall()
    assert len(rows) == 4
    assert all(r["zone"] == ZONE_LIBRE for r in rows)


def test_planning_zones_table_is_seeded(db_with_candidates: Path) -> None:
    """Les 3 zones doctrinales sont seedées par init_schema."""
    with db_session(db_with_candidates) as conn:
        rows = conn.execute(
            "SELECT zone_id FROM planning_zones ORDER BY sort_order"
        ).fetchall()
    assert [r["zone_id"] for r in rows] == [ZONE_LIBRE, ZONE_NEGOCIABLE, ZONE_GELEE]


def test_move_libre_to_negociable_succeeds(db_with_candidates: Path) -> None:
    with db_session(db_with_candidates) as conn:
        cid = conn.execute(
            "SELECT candidate_id FROM candidate_orders LIMIT 1"
        ).fetchone()["candidate_id"]
        t = move_candidate_to_zone(
            conn, cid, ZONE_NEGOCIABLE, decision="PASS", actor="test"
        )
        zone = current_zone(conn, cid)
    assert t.from_zone == ZONE_LIBRE
    assert t.to_zone == ZONE_NEGOCIABLE
    assert zone == ZONE_NEGOCIABLE


def test_move_negociable_to_gelee_succeeds(db_with_candidates: Path) -> None:
    with db_session(db_with_candidates) as conn:
        cid = conn.execute(
            "SELECT candidate_id FROM candidate_orders LIMIT 1"
        ).fetchone()["candidate_id"]
        move_candidate_to_zone(conn, cid, ZONE_NEGOCIABLE, decision="PASS")
        t = move_candidate_to_zone(conn, cid, ZONE_GELEE, decision="FREEZE")
        zone = current_zone(conn, cid)
    assert t.from_zone == ZONE_NEGOCIABLE
    assert t.to_zone == ZONE_GELEE
    assert zone == ZONE_GELEE


def test_direct_libre_to_gelee_is_rejected(db_with_candidates: Path) -> None:
    """Saut direct libre -> gelee interdit (doit passer par negociable)."""
    with db_session(db_with_candidates) as conn:
        cid = conn.execute(
            "SELECT candidate_id FROM candidate_orders LIMIT 1"
        ).fetchone()["candidate_id"]
        with pytest.raises(ValueError, match="Transition non autorisée"):
            move_candidate_to_zone(conn, cid, ZONE_GELEE)


def test_invalid_zone_name_is_rejected(db_with_candidates: Path) -> None:
    with db_session(db_with_candidates) as conn:
        cid = conn.execute(
            "SELECT candidate_id FROM candidate_orders LIMIT 1"
        ).fetchone()["candidate_id"]
        with pytest.raises(ValueError, match="Zone cible inconnue"):
            move_candidate_to_zone(conn, cid, "xyz")


def test_move_unknown_candidate_raises(db_with_candidates: Path) -> None:
    with db_session(db_with_candidates) as conn:
        with pytest.raises(ValueError, match="Candidate inconnu"):
            move_candidate_to_zone(conn, "CND-NONEXISTENT", ZONE_NEGOCIABLE)


def test_move_to_same_zone_raises(db_with_candidates: Path) -> None:
    with db_session(db_with_candidates) as conn:
        cid = conn.execute(
            "SELECT candidate_id FROM candidate_orders LIMIT 1"
        ).fetchone()["candidate_id"]
        with pytest.raises(ValueError, match="déjà en zone"):
            move_candidate_to_zone(conn, cid, ZONE_LIBRE)


def test_reverse_gelee_to_negociable_is_allowed(db_with_candidates: Path) -> None:
    """Sens retour gelee -> negociable autorisé (forme A P3 inverse, L1.6)."""
    with db_session(db_with_candidates) as conn:
        cid = conn.execute(
            "SELECT candidate_id FROM candidate_orders LIMIT 1"
        ).fetchone()["candidate_id"]
        move_candidate_to_zone(conn, cid, ZONE_NEGOCIABLE)
        move_candidate_to_zone(conn, cid, ZONE_GELEE)
        t = move_candidate_to_zone(
            conn, cid, ZONE_NEGOCIABLE, decision="RETOUR_NEGOCIABLE"
        )
        zone = current_zone(conn, cid)
    assert t.to_zone == ZONE_NEGOCIABLE
    assert zone == ZONE_NEGOCIABLE


def test_fetch_in_zone_filters_correctly(db_with_candidates: Path) -> None:
    with db_session(db_with_candidates) as conn:
        all_cands = conn.execute("SELECT candidate_id FROM candidate_orders").fetchall()
        # Move one to negociable
        move_candidate_to_zone(conn, all_cands[0]["candidate_id"], ZONE_NEGOCIABLE)
        libre = fetch_in_zone(conn, ZONE_LIBRE)
        negociable = fetch_in_zone(conn, ZONE_NEGOCIABLE)
    assert len(libre) == 3
    assert len(negociable) == 1
    assert negociable[0]["candidate_id"] == all_cands[0]["candidate_id"]


def test_transitions_for_returns_chronological_history(
    db_with_candidates: Path,
) -> None:
    with db_session(db_with_candidates) as conn:
        cid = conn.execute(
            "SELECT candidate_id FROM candidate_orders LIMIT 1"
        ).fetchone()["candidate_id"]
        move_candidate_to_zone(conn, cid, ZONE_NEGOCIABLE, decision="PASS", actor="alice")
        move_candidate_to_zone(conn, cid, ZONE_GELEE, decision="FREEZE", actor="bob")
        history = transitions_for(conn, cid)
    assert len(history) == 2
    assert history[0].to_zone == ZONE_NEGOCIABLE
    assert history[0].decision == "PASS"
    assert history[0].actor == "alice"
    assert history[1].to_zone == ZONE_GELEE
    assert history[1].decision == "FREEZE"


def test_v0_planner_does_not_change_zone(tmp_db: Path, fixtures_dir: Path) -> None:
    """Le planner V0 (P1 legacy) ne touche pas la zone - les candidats restent en libre."""
    from pilotage_flux.gates import run_p1_promotion

    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_dir)
        run_p1_promotion(conn)
        zones = conn.execute(
            "SELECT DISTINCT zone FROM candidate_orders"
        ).fetchall()
    assert len(zones) == 1
    assert zones[0]["zone"] == ZONE_LIBRE
