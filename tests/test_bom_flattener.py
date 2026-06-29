"""Tests de l'aplatissement multi-niveau des nomenclatures."""

from pathlib import Path

import pytest

from pilotage_flux.aps import (
    flatten_bom_for_article,
    get_manufactured_components,
    get_purchased_components,
    persist_flattened_bom,
)
from pilotage_flux.db import db_session
from pilotage_flux.importers import import_referentials


@pytest.fixture
def db_v1(tmp_db: Path, fixtures_v1_dir: Path) -> Path:
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
    return tmp_db


def test_flatten_two_levels_returns_all_descendants(db_v1: Path) -> None:
    with db_session(db_v1) as conn:
        nodes = flatten_bom_for_article(conn, "ART-A")
    # ART-A -> SEMI-1 (1) + COMP-Y (1) ; SEMI-1 -> COMP-X (2)
    # Descendants attendus : SEMI-1, COMP-Y, COMP-X
    components = {n.component_article for n in nodes}
    assert components == {"SEMI-1", "COMP-Y", "COMP-X"}


def test_flatten_quantities_are_cumulative(db_v1: Path) -> None:
    with db_session(db_v1) as conn:
        nodes = flatten_bom_for_article(conn, "ART-A")
    by_component = {n.component_article: n for n in nodes}
    # 1 SEMI-1 par ART-A
    assert by_component["SEMI-1"].cumulative_quantity == 1.0
    # 1 COMP-Y par ART-A
    assert by_component["COMP-Y"].cumulative_quantity == 1.0
    # 2 COMP-X par SEMI-1 * 1 SEMI-1 par ART-A = 2 COMP-X par ART-A
    assert by_component["COMP-X"].cumulative_quantity == 2.0


def test_flatten_depth_levels(db_v1: Path) -> None:
    with db_session(db_v1) as conn:
        nodes = flatten_bom_for_article(conn, "ART-A")
    by_component = {n.component_article: n for n in nodes}
    assert by_component["SEMI-1"].depth_level == 1
    assert by_component["COMP-Y"].depth_level == 1
    assert by_component["COMP-X"].depth_level == 2


def test_flatten_distinguishes_leaf_from_intermediate(db_v1: Path) -> None:
    with db_session(db_v1) as conn:
        nodes = flatten_bom_for_article(conn, "ART-A")
    by_component = {n.component_article: n for n in nodes}
    assert by_component["SEMI-1"].is_leaf is False  # fabrique
    assert by_component["COMP-Y"].is_leaf is True  # achete
    assert by_component["COMP-X"].is_leaf is True  # achete


def test_flatten_paths_trace_origin(db_v1: Path) -> None:
    with db_session(db_v1) as conn:
        nodes = flatten_bom_for_article(conn, "ART-A")
    paths = {n.component_article: n.path for n in nodes}
    assert paths["SEMI-1"] == "/ART-A/SEMI-1"
    assert paths["COMP-Y"] == "/ART-A/COMP-Y"
    assert paths["COMP-X"] == "/ART-A/SEMI-1/COMP-X"


def test_get_manufactured_components_filters_correctly(db_v1: Path) -> None:
    with db_session(db_v1) as conn:
        intermediates = get_manufactured_components(conn, "ART-A")
    assert {n.component_article for n in intermediates} == {"SEMI-1"}


def test_get_purchased_components_filters_correctly(db_v1: Path) -> None:
    with db_session(db_v1) as conn:
        leaves = get_purchased_components(conn, "ART-A")
    assert {n.component_article for n in leaves} == {"COMP-Y", "COMP-X"}


def test_persist_flattened_bom_fills_table(db_v1: Path) -> None:
    with db_session(db_v1) as conn:
        n = persist_flattened_bom(conn)
        rows = conn.execute(
            "SELECT root_article, component_article FROM flattened_bom_lines "
            "ORDER BY root_article, depth_level, component_article"
        ).fetchall()
    # ART-A : 3 descendants ; SEMI-1 : 1 descendant (COMP-X)
    assert n == 4
    by_root: dict[str, set[str]] = {}
    for r in rows:
        by_root.setdefault(r["root_article"], set()).add(r["component_article"])
    assert by_root["ART-A"] == {"SEMI-1", "COMP-Y", "COMP-X"}
    assert by_root["SEMI-1"] == {"COMP-X"}


def test_persist_is_idempotent(db_v1: Path) -> None:
    with db_session(db_v1) as conn:
        first = persist_flattened_bom(conn)
        second = persist_flattened_bom(conn)
        (total,) = conn.execute(
            "SELECT COUNT(*) FROM flattened_bom_lines"
        ).fetchone()
    assert first == second == 4
    assert total == 4  # pas de doublon


def test_flatten_detects_cycle(tmp_db: Path) -> None:
    """Une BOM cyclique leve ValueError."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO articles (article_id, label, is_purchased) VALUES ('A', 'A', 0)"
        )
        conn.execute(
            "INSERT INTO articles (article_id, label, is_purchased) VALUES ('B', 'B', 0)"
        )
        conn.execute(
            "INSERT INTO bom_lines (parent_article, child_article, quantity) "
            "VALUES ('A', 'B', 1)"
        )
        conn.execute(
            "INSERT INTO bom_lines (parent_article, child_article, quantity) "
            "VALUES ('B', 'A', 1)"
        )
        with pytest.raises(ValueError, match="[Cc]ycle"):
            flatten_bom_for_article(conn, "A")
