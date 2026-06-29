"""Tests des 5 familles de flux (L6.2 / cadrage §12)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pilotage_flux.aps import compute_candidates
from pilotage_flux.db import db_session
from pilotage_flux.events_v3 import (
    attach_causes_to_deviation,
    evaluate_all_open_deviations,
    generate_expected_from_batch,
    list_deviations,
    match_actuals_to_expected,
)
from pilotage_flux.flux import (
    compute_coherence,
    compute_smoothing,
    create_contract,
)
from pilotage_flux.gates import (
    run_p1_promotion,
    run_p2_on_libre_zone,
    run_p3_freeze,
)
from pilotage_flux.importers import import_referentials
from pilotage_flux.mes import close_of, finish_operation, launch_of, start_operation
from pilotage_flux.risk_debt import extinguish_risk_debt, list_risk_debts
from pilotage_flux.visualization import (
    decision_flow_view,
    event_flow_view,
    material_flow_view,
    of_detail_view,
    quality_flow_view,
    workstation_view,
)


@pytest.fixture
def session_with_v3_pipeline(tmp_db: Path, fixtures_v1_dir: Path) -> Path:
    """Prépare une session V3 complète : import + plan + freeze + exécution
    + matching → produit toutes les données nécessaires aux 5 familles."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        compute_candidates(conn)
        # P1 d'abord pour créer les OFs
        run_p1_promotion(conn)
        run_p2_on_libre_zone(conn)
        cids = [
            r["candidate_id"]
            for r in conn.execute(
                "SELECT candidate_id FROM candidate_orders ORDER BY candidate_id"
            )
        ]
        contract = create_contract(
            conn, horizon_label="W27",
            horizon_start="2026-07-06", horizon_end="2026-07-12",
            candidate_ids=cids,
        )
        compute_coherence(conn, contract.contract_id)
        compute_smoothing(conn, contract.contract_id)
        for d in list_risk_debts(conn, status="open"):
            extinguish_risk_debt(conn, d.risk_debt_id, reason="test")
        result = run_p3_freeze(conn, contract.contract_id)
        # Génération expected events AVANT exécution
        generate_expected_from_batch(conn, result.batch_id)
        # Exécute 2 OFs pour générer des événements réels
        for of_id in conn.execute(
            "SELECT of_id FROM manufacturing_orders LIMIT 2"
        ).fetchall():
            launch_of(conn, of_id["of_id"])
            ops = conn.execute(
                "SELECT of_op_id FROM order_operations WHERE of_id = ? "
                "ORDER BY sequence_idx",
                (of_id["of_id"],),
            ).fetchall()
            for op in ops:
                start_operation(conn, op["of_op_id"])
                finish_operation(
                    conn, op["of_op_id"], qty_good=95, qty_scrap=5
                )
            close_of(conn, of_id["of_id"])
        # Matching APRÈS exécution
        match_actuals_to_expected(conn, result.batch_id)
        for d in list_deviations(conn):
            if not d.is_absorbed:
                attach_causes_to_deviation(conn, d.deviation_id)
        evaluate_all_open_deviations(conn, batch_id=result.batch_id)
    return tmp_db


def test_family_1_physical_workstation_view(session_with_v3_pipeline: Path) -> None:
    """Famille 1a — flux physique par poste."""
    with db_session(session_with_v3_pipeline) as conn:
        views = workstation_view(conn)
    assert len(views) == 3  # WS-1, WS-2, WS-3
    # Au moins un poste avec des ops done (les 2 OFs exécutés)
    total_done = sum(len(v.done) for v in views)
    assert total_done > 0


def test_family_1_physical_of_detail(session_with_v3_pipeline: Path) -> None:
    """Famille 1b — flux physique par OF."""
    with db_session(session_with_v3_pipeline) as conn:
        any_of = conn.execute(
            "SELECT of_id FROM manufacturing_orders LIMIT 1"
        ).fetchone()
        detail = of_detail_view(conn, any_of["of_id"])
    assert detail is not None
    assert detail.of_id == any_of["of_id"]
    assert len(detail.operations) > 0
    assert len(detail.events) > 0


def test_family_2_material_flow(session_with_v3_pipeline: Path) -> None:
    """Famille 2 — flux matière (stocks + PO + conso vs théorique)."""
    with db_session(session_with_v3_pipeline) as conn:
        # Setup stock minimal pour tester
        conn.execute(
            "INSERT OR REPLACE INTO stocks "
            "(article_id, qty_available, qty_reserved) "
            "VALUES ('COMP-X', 100, 0)"
        )
        report = material_flow_view(conn)
    assert len(report.items) == 4  # ART-A, SEMI-1, COMP-X, COMP-Y
    items_by_id = {i.article_id: i for i in report.items}
    assert items_by_id["COMP-X"].qty_on_hand == 100.0
    # Les articles fabriqués ont une qty_theoretical > 0 (BOM × OF)
    assert items_by_id["SEMI-1"].qty_theoretical > 0


def test_family_3_quality_flow(session_with_v3_pipeline: Path) -> None:
    """Famille 3 — flux qualité (yield rate, NCs)."""
    with db_session(session_with_v3_pipeline) as conn:
        report = quality_flow_view(conn)
    # Les 2 OFs exécutés ont qty_good=95, qty_scrap=5 → yield = 95%
    closed = [i for i in report.items if i.qty_good > 0]
    assert len(closed) > 0
    for item in closed:
        assert 0.9 <= item.yield_rate <= 1.0
    # Pas de NC ouvert dans le pipeline standard
    assert report.total_nc == 0


def test_family_4_decision_flow(session_with_v3_pipeline: Path) -> None:
    """Famille 4 — flux décisionnel (portes + zones + filtre dual)."""
    with db_session(session_with_v3_pipeline) as conn:
        report = decision_flow_view(conn)
    # Au moins une décision P1, P2, P3, P4 + transitions zone
    gates_seen = {d.gate for d in report.gate_decisions}
    assert "P1" in gates_seen
    assert "P3" in gates_seen
    assert len(report.zone_transitions) > 0
    # Filtre dual : décisions présentes (matching produit des deviations)
    assert len(report.tolerance_actions) > 0
    by_level = report.actions_by_level()
    assert sum(by_level.values()) == len(report.tolerance_actions)


def test_family_5_event_flow(session_with_v3_pipeline: Path) -> None:
    """Famille 5 — flux événementiel (attendus vs réels + causes)."""
    with db_session(session_with_v3_pipeline) as conn:
        report = event_flow_view(conn)
    assert report.total_expected > 0
    assert report.total_matched > 0
    # Au moins une cause attachée à au moins une ligne matched
    causes = [l for l in report.lines if l.cause_label is not None]
    assert len(causes) > 0


def test_event_flow_filter_by_batch(session_with_v3_pipeline: Path) -> None:
    """Filtre par batch_id : isole une tranche gelée."""
    with db_session(session_with_v3_pipeline) as conn:
        batch_row = conn.execute(
            "SELECT batch_id FROM freeze_batches LIMIT 1"
        ).fetchone()
        report = event_flow_view(conn, batch_id=batch_row["batch_id"])
    assert report.total_expected > 0
