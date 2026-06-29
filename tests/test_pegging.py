"""Tests du pegging multi-niveau."""

from pathlib import Path

import pytest

from pilotage_flux.aps import (
    add_pegging_link,
    compute_candidates,
    get_incoming,
    get_outgoing,
    get_pegging_chain,
    get_root_demand,
)
from pilotage_flux.db import db_session
from pilotage_flux.importers import import_referentials


@pytest.fixture
def db_v1_after_cbn(tmp_db: Path, fixtures_v1_dir: Path) -> Path:
    """Base avec CBN multi-niveau execute."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        compute_candidates(conn)
    return tmp_db


def test_cbn_creates_candidates_for_each_manufactured_article(
    db_v1_after_cbn: Path,
) -> None:
    """Pour 2 SO de ART-A, on attend 2 ART-A + 2 SEMI-1 = 4 candidates."""
    with db_session(db_v1_after_cbn) as conn:
        rows = conn.execute(
            "SELECT article_id, COUNT(*) AS n FROM candidate_orders GROUP BY article_id"
        ).fetchall()
    by_article = {r["article_id"]: r["n"] for r in rows}
    assert by_article == {"ART-A": 2, "SEMI-1": 2}


def test_cbn_candidates_have_correct_quantities(db_v1_after_cbn: Path) -> None:
    """SEMI-1 quantites = 100 (depuis SO-001) et 50 (depuis SO-002)."""
    with db_session(db_v1_after_cbn) as conn:
        rows = conn.execute(
            "SELECT quantity FROM candidate_orders WHERE article_id = 'SEMI-1' "
            "ORDER BY quantity DESC"
        ).fetchall()
    assert [r["quantity"] for r in rows] == [100.0, 50.0]


def test_pegging_outgoing_from_sales_order(db_v1_after_cbn: Path) -> None:
    """SO-001 doit avoir un lien direct vers le candidate de ART-A."""
    with db_session(db_v1_after_cbn) as conn:
        links = get_outgoing(conn, "sales_order", "SO-001")
    assert len(links) == 1
    assert links[0].target_type == "candidate_order"
    assert links[0].article_id == "ART-A"
    assert links[0].quantity == 100.0


def test_pegging_chain_traverses_all_levels(db_v1_after_cbn: Path) -> None:
    """Chaine SO-001 : ART-A -> SEMI-1 -> {COMP-X, COMP-Y}."""
    with db_session(db_v1_after_cbn) as conn:
        chain = get_pegging_chain(conn, "sales_order", "SO-001")

    # Liens attendus :
    #   SO-001 -> CND(ART-A)           depth 0
    #   CND(ART-A) -> CND(SEMI-1)      depth 1
    #   CND(ART-A) -> component COMP-Y depth 1
    #   CND(SEMI-1) -> component COMP-X depth 2
    targets = [(l.target_type, l.article_id, l.quantity) for l in chain]
    assert ("candidate_order", "ART-A", 100.0) in targets
    assert ("candidate_order", "SEMI-1", 100.0) in targets
    assert ("component", "COMP-Y", 100.0) in targets
    assert ("component", "COMP-X", 200.0) in targets  # 2 par SEMI-1 * 100


def test_pegging_chain_so2_independent_from_so1(db_v1_after_cbn: Path) -> None:
    """SO-002 a sa propre chaine avec quantites 50."""
    with db_session(db_v1_after_cbn) as conn:
        chain = get_pegging_chain(conn, "sales_order", "SO-002")
    quantities = {(l.article_id, l.quantity) for l in chain}
    assert ("ART-A", 50.0) in quantities
    assert ("SEMI-1", 50.0) in quantities
    assert ("COMP-X", 100.0) in quantities  # 2 * 50
    assert ("COMP-Y", 50.0) in quantities


def test_root_demand_lookup_from_component_candidate(
    db_v1_after_cbn: Path,
) -> None:
    """Depuis un candidate SEMI-1, on remonte au sales_order d'origine."""
    with db_session(db_v1_after_cbn) as conn:
        semi_cand = conn.execute(
            "SELECT candidate_id FROM candidate_orders WHERE article_id = 'SEMI-1' "
            "AND quantity = 100"
        ).fetchone()
        root = get_root_demand(conn, "candidate_order", semi_cand["candidate_id"])
    assert root == ("sales_order", "SO-001")


def test_incoming_link_lookup(db_v1_after_cbn: Path) -> None:
    """Un candidate SEMI-1 doit avoir 1 lien entrant depuis son parent ART-A."""
    with db_session(db_v1_after_cbn) as conn:
        semi_cand = conn.execute(
            "SELECT candidate_id FROM candidate_orders WHERE article_id = 'SEMI-1' "
            "AND quantity = 100"
        ).fetchone()
        incoming = get_incoming(conn, "candidate_order", semi_cand["candidate_id"])
    assert len(incoming) == 1
    assert incoming[0].source_type == "candidate_order"
    assert incoming[0].article_id == "SEMI-1"


def test_add_pegging_link_persists(tmp_db: Path) -> None:
    """Smoke test : un lien ajoute est retrouvable."""
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO articles (article_id, label) VALUES ('ART-X', 'Art X')"
        )
        pid = add_pegging_link(
            conn,
            source_type="sales_order",
            source_id="SO-X",
            target_type="candidate_order",
            target_id="CND-Z",
            article_id="ART-X",
            quantity=42.0,
        )
        out = get_outgoing(conn, "sales_order", "SO-X")
    assert pid > 0
    assert len(out) == 1
    assert out[0].quantity == 42.0
