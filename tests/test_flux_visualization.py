"""Étape 3 — 5 flux de visualisation du cadrage v1.3 §12."""

from __future__ import annotations

import pytest

from pilotage_flux.cybernetic.delta_engine.levels import (
    seed_default_delta_levels,
)
from pilotage_flux.db import db_session
from pilotage_flux.flux_visualization import (
    FluxEdge,
    FluxGraph,
    FluxNode,
    build_all_flux,
    build_flux_decision,
    build_flux_documentaire,
    build_flux_information,
    build_flux_physique,
    build_flux_qualite,
)


# ---------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------

def test_flux_node_to_dict() -> None:
    n = FluxNode(id="ws:1", label="WS-1", kind="workstation",
                  attributes={"wip": 3})
    d = n.to_dict()
    assert d == {
        "id": "ws:1", "label": "WS-1",
        "kind": "workstation", "attributes": {"wip": 3},
    }


def test_flux_edge_to_dict() -> None:
    e = FluxEdge(source="a", target="b", kind="link", label="x")
    d = e.to_dict()
    assert d == {
        "source": "a", "target": "b",
        "kind": "link", "label": "x", "attributes": {},
    }


def test_flux_graph_basic_properties() -> None:
    g = FluxGraph(flux="test")
    g.nodes.append(FluxNode(id="a", label="A", kind="x"))
    g.edges.append(FluxEdge(source="a", target="b", kind="y"))
    assert g.n_nodes == 1
    assert g.n_edges == 1
    d = g.to_dict()
    assert d["flux"] == "test"


# ---------------------------------------------------------------------
# Flux physique
# ---------------------------------------------------------------------

def _seed_minimal_physical(conn):
    conn.execute(
        "INSERT INTO articles (article_id, label) VALUES ('A', 'a')"
    )
    conn.execute(
        "INSERT INTO workstations (workstation_id, label, sequence_idx) "
        "VALUES ('WS-1', 'P1', 1), ('WS-2', 'P2', 2)"
    )
    conn.execute(
        "INSERT INTO manufacturing_orders "
        "(of_id, article_id, quantity, status) "
        "VALUES ('OF-1', 'A', 100, 'launched')"
    )
    conn.execute(
        "INSERT INTO order_operations "
        "(of_op_id, of_id, sequence_idx, workstation_id, "
        " unit_time_min, status) "
        "VALUES (1, 'OF-1', 1, 'WS-1', 2.0, 'planned')"
    )


def test_flux_physique_includes_workstations(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _seed_minimal_physical(conn)
        g = build_flux_physique(conn)
        ws_nodes = [n for n in g.nodes if n.kind == "workstation"]
        assert len(ws_nodes) == 2


def test_flux_physique_ws_precedence_edges(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _seed_minimal_physical(conn)
        g = build_flux_physique(conn)
        prec = [e for e in g.edges if e.kind == "ws_precedence"]
        assert len(prec) == 1
        assert prec[0].source == "ws:WS-1"
        assert prec[0].target == "ws:WS-2"


def test_flux_physique_of_links_to_current_ws(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _seed_minimal_physical(conn)
        g = build_flux_physique(conn)
        of_at_ws = [e for e in g.edges if e.kind == "of_at_ws"]
        assert len(of_at_ws) == 1
        assert of_at_ws[0].source == "of:OF-1"
        assert of_at_ws[0].target == "ws:WS-1"


def test_flux_physique_closed_of_excluded(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _seed_minimal_physical(conn)
        conn.execute(
            "UPDATE manufacturing_orders SET status = 'closed' "
            "WHERE of_id = 'OF-1'"
        )
        g = build_flux_physique(conn)
        of_nodes = [n for n in g.nodes if n.kind == "of"]
        assert of_nodes == []


# ---------------------------------------------------------------------
# Flux information
# ---------------------------------------------------------------------

def test_flux_information_so_candidate_contract_chain(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO articles (article_id, label) VALUES ('A', 'a')"
        )
        conn.execute(
            "INSERT INTO sales_orders "
            "(sales_order_id, article_id, quantity, due_date) "
            "VALUES ('SO-1', 'A', 100, '2026-12-01')"
        )
        conn.execute(
            "INSERT INTO candidate_orders "
            "(candidate_id, sales_order_id, article_id, quantity, "
            " status, zone) "
            "VALUES ('CAND-1', 'SO-1', 'A', 100, 'candidate', 'libre')"
        )
        conn.execute(
            "INSERT INTO flux_contracts "
            "(contract_id, horizon_label, horizon_start, horizon_end) "
            "VALUES ('FX-1', 'W27', '2026-07-01', '2026-07-08')"
        )
        conn.execute(
            "INSERT INTO flux_contract_versions "
            "(contract_id, version, total_quantity) "
            "VALUES ('FX-1', 1, 100)"
        )
        conn.execute(
            "INSERT INTO flux_contract_links "
            "(contract_id, version, candidate_id, qty_in_contract) "
            "VALUES ('FX-1', 1, 'CAND-1', 100)"
        )
        g = build_flux_information(conn)
        kinds = {n.kind for n in g.nodes}
        assert "sales_order" in kinds
        assert "candidate" in kinds
        assert "flux_contract" in kinds
        # Chaîne SO → CAND → FX
        so_to_cand = [
            e for e in g.edges if e.kind == "so_to_candidate"
        ]
        assert len(so_to_cand) == 1
        cand_in_fx = [
            e for e in g.edges if e.kind == "candidate_in_contract"
        ]
        assert len(cand_in_fx) == 1


# ---------------------------------------------------------------------
# Flux décision
# ---------------------------------------------------------------------

def test_flux_decision_has_5_gates(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        g = build_flux_decision(conn)
        gates = [n for n in g.nodes if n.kind == "gate"]
        gate_ids = {n.id for n in gates}
        assert gate_ids == {
            "gate:P1", "gate:P2", "gate:P3",
            "gate:P3inv", "gate:P4",
        }


def test_flux_decision_includes_delta_levels(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        seed_default_delta_levels(conn)
        g = build_flux_decision(conn)
        levels = [n for n in g.nodes if n.kind == "delta_level"]
        assert len(levels) == 6
        codes = {n.id for n in levels}
        assert codes == {
            "niveau:L1", "niveau:L2", "niveau:L3",
            "niveau:L4", "niveau:L5", "niveau:L6",
        }


def test_flux_decision_aggregates_gate_decisions(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        # 2 décisions P3 FREEZE + 1 P3 RENEGOTIATE
        for dec in ("FREEZE", "FREEZE", "RENEGOTIATE"):
            conn.execute(
                "INSERT INTO gate_decisions "
                "(gate, subject_type, subject_id, decision) "
                "VALUES ('P3', 'of', 'X', ?)",
                (dec,),
            )
        g = build_flux_decision(conn)
        gd_nodes = [n for n in g.nodes if n.kind == "gate_decision"]
        freeze = next(n for n in gd_nodes if "FREEZE" in n.label)
        assert freeze.attributes["count"] == 2


# ---------------------------------------------------------------------
# Flux documentaire
# ---------------------------------------------------------------------

def test_flux_documentaire_articles_and_bom(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        for a in ("PARENT", "CHILD"):
            conn.execute(
                "INSERT INTO articles (article_id, label) VALUES (?, ?)",
                (a, a.lower()),
            )
        conn.execute(
            "INSERT INTO bom_lines "
            "(parent_article, child_article, quantity) "
            "VALUES ('PARENT', 'CHILD', 2)"
        )
        g = build_flux_documentaire(conn)
        arts = [n for n in g.nodes if n.kind == "article"]
        assert len(arts) == 2
        bom = [e for e in g.edges if e.kind == "bom_composition"]
        assert len(bom) == 1
        assert bom[0].source == "art:CHILD"
        assert bom[0].target == "art:PARENT"


def test_flux_documentaire_weight_versions(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO weight_versions "
            "(label, description, coefficients_json, status) "
            "VALUES ('v1', 'init', '{}', 'active')"
        )
        g = build_flux_documentaire(conn)
        wv = [n for n in g.nodes if n.kind == "weight_version"]
        assert len(wv) == 1
        assert wv[0].attributes["status"] == "active"


# ---------------------------------------------------------------------
# Flux qualité
# ---------------------------------------------------------------------

def test_flux_qualite_quality_events(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO articles (article_id, label) VALUES ('A', 'a')"
        )
        conn.execute(
            "INSERT INTO manufacturing_orders "
            "(of_id, article_id, quantity, status) "
            "VALUES ('OF-1', 'A', 100, 'launched')"
        )
        conn.execute(
            "INSERT INTO quality_events "
            "(of_id, event_type, severity, qty_concerned) "
            "VALUES ('OF-1', 'nc', 'normal', 10)"
        )
        g = build_flux_qualite(conn)
        qe = [n for n in g.nodes if n.kind == "quality_event"]
        assert len(qe) == 1
        assert qe[0].attributes["severity"] == "normal"
        # Edge OF → QE
        edge = next(
            e for e in g.edges if e.kind == "of_has_quality_event"
        )
        assert edge.source == "of:OF-1"


def test_flux_qualite_pc_breaches(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO articles (article_id, label) VALUES ('A', 'a')"
        )
        conn.execute(
            "INSERT INTO workstations (workstation_id, label, sequence_idx) "
            "VALUES ('WS-1', 'P1', 1)"
        )
        conn.execute(
            "INSERT INTO manufacturing_orders "
            "(of_id, article_id, quantity, status) "
            "VALUES ('OF-1', 'A', 100, 'in_progress')"
        )
        conn.execute(
            "INSERT INTO order_operations "
            "(of_op_id, of_id, sequence_idx, workstation_id, "
            " unit_time_min, status) "
            "VALUES (1, 'OF-1', 1, 'WS-1', 2.0, 'planned')"
        )
        conn.execute(
            "INSERT INTO production_contracts "
            "(of_id, of_op_id, target_time_min, target_qty_good, "
            " origin_kind, origin_ref, status, breach_dimensions) "
            "VALUES ('OF-1', 1, 200, 100, 'sales_order', 'SO-1', "
            "        'breached', 'T,Er')"
        )
        g = build_flux_qualite(conn)
        breaches = [n for n in g.nodes if n.kind == "pc_breach"]
        assert len(breaches) == 1


# ---------------------------------------------------------------------
# All-in-one
# ---------------------------------------------------------------------

def test_build_all_flux_returns_five(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        flux = build_all_flux(conn)
        assert set(flux.keys()) == {
            "physique", "information", "decision",
            "documentaire", "qualite",
        }
        for name, g in flux.items():
            assert g.flux == name


def test_build_all_flux_empty_db_does_not_crash(tmp_db) -> None:
    """Sur une base vide, chaque builder renvoie un graphe (potentiel-
    lement avec quelques nœuds de référence pour les gates)."""
    with db_session(tmp_db) as conn:
        flux = build_all_flux(conn)
        # Flux décision a 5 gates de référence même sans données
        assert flux["decision"].n_nodes >= 5
