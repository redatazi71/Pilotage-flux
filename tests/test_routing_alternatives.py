"""Tests des routings alternatifs (V2 : parallele + hybride)."""

from pathlib import Path

import pytest

from pilotage_flux.aps import (
    add_alternative,
    available_workstations_for,
    list_alternatives_for,
    pick_workstation,
)
from pilotage_flux.db import db_session
from pilotage_flux.importers import import_referentials


@pytest.fixture
def db_v1(tmp_db: Path, fixtures_v1_dir: Path) -> Path:
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
    return tmp_db


def test_add_alternative_persists(db_v1: Path) -> None:
    with db_session(db_v1) as conn:
        # ART-A op 1 sur WS-2 par defaut ; on ajoute WS-3 en alternatif
        alt = add_alternative(
            conn, article_id="ART-A", sequence_idx=1,
            workstation_id="WS-3", unit_time_min=4.0,
            preference_order=50,
        )
        listing = list_alternatives_for(conn, "ART-A", 1)
    assert alt.workstation_id == "WS-3"
    assert alt.preference_order == 50
    assert len(listing) == 1


def test_add_alternative_refuses_invalid_args(db_v1: Path) -> None:
    with db_session(db_v1) as conn:
        with pytest.raises(ValueError, match="strictement positif"):
            add_alternative(
                conn, article_id="ART-A", sequence_idx=1,
                workstation_id="WS-1", unit_time_min=0,
            )
        with pytest.raises(ValueError, match="Article inconnu"):
            add_alternative(
                conn, article_id="X", sequence_idx=1,
                workstation_id="WS-1", unit_time_min=1.0,
            )
        with pytest.raises(ValueError, match="Workstation inconnue"):
            add_alternative(
                conn, article_id="ART-A", sequence_idx=1,
                workstation_id="WS-X", unit_time_min=1.0,
            )


def test_available_workstations_combines_main_and_alternatives(db_v1: Path) -> None:
    """ART-A op 1 a un routing principal (WS-2) + une alternative (WS-3)."""
    with db_session(db_v1) as conn:
        # Routing principal V1 fixtures : ART-A op 1 -> WS-2 (3 min)
        add_alternative(
            conn, article_id="ART-A", sequence_idx=1,
            workstation_id="WS-3", unit_time_min=4.0,
            preference_order=10,
        )
        choices = available_workstations_for(conn, "ART-A", 1)
    workstations = {c.workstation_id for c in choices}
    assert workstations == {"WS-2", "WS-3"}
    # Le main a preference_order 0, donc il vient en premier
    assert choices[0].workstation_id == "WS-2"
    assert choices[0].source == "main"


def test_pick_workstation_preferred_uses_preference_order(db_v1: Path) -> None:
    """Une alternative avec preference_order < 0 doit primer sur le main."""
    with db_session(db_v1) as conn:
        add_alternative(
            conn, article_id="ART-A", sequence_idx=1,
            workstation_id="WS-1", unit_time_min=5.0,
            preference_order=-10,  # plus prioritaire que le main (0)
        )
        chosen = pick_workstation(conn, "ART-A", 1, strategy="preferred")
    assert chosen is not None
    assert chosen.workstation_id == "WS-1"
    assert chosen.source == "alt"


def test_pick_workstation_fastest_picks_lowest_unit_time(db_v1: Path) -> None:
    with db_session(db_v1) as conn:
        # ART-A op 1 main = WS-2, 3.0 min ; ajout WS-3 a 1.0 min
        add_alternative(
            conn, article_id="ART-A", sequence_idx=1,
            workstation_id="WS-3", unit_time_min=1.0,
            preference_order=200,  # mauvaise pref mais plus rapide
        )
        chosen = pick_workstation(conn, "ART-A", 1, strategy="fastest")
    assert chosen.workstation_id == "WS-3"


def test_pick_workstation_returns_none_when_no_routing(db_v1: Path) -> None:
    with db_session(db_v1) as conn:
        chosen = pick_workstation(conn, "ART-A", 99, strategy="preferred")
    assert chosen is None


def test_pick_workstation_unknown_strategy_raises(db_v1: Path) -> None:
    with db_session(db_v1) as conn:
        with pytest.raises(ValueError, match="strategy"):
            pick_workstation(conn, "ART-A", 1, strategy="xxx")


def test_unique_constraint_article_seq_workstation(db_v1: Path) -> None:
    """On ne peut pas ajouter deux fois la meme (article, seq, ws)."""
    import sqlite3
    with db_session(db_v1) as conn:
        add_alternative(
            conn, article_id="ART-A", sequence_idx=1,
            workstation_id="WS-3", unit_time_min=4.0,
        )
        with pytest.raises(sqlite3.IntegrityError):
            add_alternative(
                conn, article_id="ART-A", sequence_idx=1,
                workstation_id="WS-3", unit_time_min=5.0,
            )
