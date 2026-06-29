"""Tests de l'absorption niveau 0 CPM (L3.3)."""

from pathlib import Path

import pytest

from pilotage_flux.aps import compute_candidates
from pilotage_flux.db import db_session
from pilotage_flux.events_v3 import (
    apply_cpm_absorption,
    generate_expected_from_batch,
    list_deviations,
    match_actuals_to_expected,
)
from pilotage_flux.flux import compute_coherence, compute_smoothing, create_contract
from pilotage_flux.gates import (
    run_p1_promotion,
    run_p2_on_libre_zone,
    run_p3_freeze,
)
from pilotage_flux.importers import import_referentials
from pilotage_flux.mes import close_of, finish_operation, launch_of, start_operation
from pilotage_flux.risk_debt import extinguish_risk_debt, list_risk_debts


@pytest.fixture
def db_v3_with_deviations(
    tmp_db: Path, fixtures_v1_dir: Path
) -> tuple[Path, str]:
    """Base avec freeze + execution + déviations calculées (avant CPM)."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        compute_candidates(conn)
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
        generate_expected_from_batch(conn, result.batch_id)

        of_id = conn.execute(
            "SELECT of_id FROM manufacturing_orders "
            "WHERE article_id = 'SEMI-1' AND quantity = 100"
        ).fetchone()["of_id"]
        launch_of(conn, of_id)
        ops = conn.execute(
            "SELECT of_op_id FROM order_operations WHERE of_id = ? ORDER BY sequence_idx",
            (of_id,),
        ).fetchall()
        for op in ops:
            start_operation(conn, op["of_op_id"])
            finish_operation(conn, op["of_op_id"], qty_good=95, qty_scrap=5)
        close_of(conn, of_id)
        match_actuals_to_expected(conn, result.batch_id)
    return tmp_db, result.batch_id


def test_default_margin_absorbs_small_deviations(
    db_v3_with_deviations: tuple[Path, str]
) -> None:
    """Marge par défaut = 60 min. Toute déviation < 60 min absolue est absorbée."""
    db_path, batch_id = db_v3_with_deviations
    with db_session(db_path) as conn:
        absorptions = apply_cpm_absorption(conn, batch_id=batch_id)
        small_devs = [a for a in absorptions if a.absolute_delta_minutes <= 60]
        large_devs = [a for a in absorptions if a.absolute_delta_minutes > 60]
    # Toute petite est absorbée
    for a in small_devs:
        assert a.is_absorbed is True
    # Aucune grande ne l'est
    for a in large_devs:
        assert a.is_absorbed is False


def test_cpm_writes_margin_used(
    db_v3_with_deviations: tuple[Path, str]
) -> None:
    """Le champ cpm_margin_used est rempli après application."""
    db_path, batch_id = db_v3_with_deviations
    with db_session(db_path) as conn:
        apply_cpm_absorption(conn, batch_id=batch_id)
        devs = list_deviations(conn)
        for d in devs:
            assert d.cpm_margin_used is not None


def test_absorbed_qualification_set_to_absorbed(
    db_v3_with_deviations: tuple[Path, str]
) -> None:
    db_path, batch_id = db_v3_with_deviations
    with db_session(db_path) as conn:
        apply_cpm_absorption(conn, batch_id=batch_id)
        devs = list_deviations(conn)
    for d in devs:
        if d.is_absorbed:
            assert d.qualification == "absorbed"


def test_workstation_margin_overrides_global(
    tmp_db: Path, fixtures_v1_dir: Path
) -> None:
    """Une marge data-driven sur un workstation prime sur la marge globale."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        # Marge globale large = 1000 min, mais WS-1 spécifique = 1 min (très strict)
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'cpm_margin_minutes', 1000)"
        )
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('workstation', 'WS-1', 'cpm_margin_minutes', 1)"
        )
        # Reconstruit le pipeline minimal pour avoir une déviation WS-1
        compute_candidates(conn)
        run_p1_promotion(conn)
        run_p2_on_libre_zone(conn)
        cids = [
            r["candidate_id"]
            for r in conn.execute(
                "SELECT candidate_id FROM candidate_orders ORDER BY candidate_id"
            )
        ]
        contract = create_contract(
            conn, horizon_label="W",
            horizon_start="2026-07-06", horizon_end="2026-07-12",
            candidate_ids=cids,
        )
        compute_coherence(conn, contract.contract_id)
        compute_smoothing(conn, contract.contract_id)
        for d in list_risk_debts(conn, status="open"):
            extinguish_risk_debt(conn, d.risk_debt_id, reason="t")
        result = run_p3_freeze(conn, contract.contract_id)
        generate_expected_from_batch(conn, result.batch_id)

        of_id = conn.execute(
            "SELECT of_id FROM manufacturing_orders "
            "WHERE article_id = 'SEMI-1' AND quantity = 100"
        ).fetchone()["of_id"]
        launch_of(conn, of_id)
        ops = conn.execute(
            "SELECT of_op_id FROM order_operations WHERE of_id = ? ORDER BY sequence_idx",
            (of_id,),
        ).fetchall()
        for op in ops:
            start_operation(conn, op["of_op_id"])
            finish_operation(conn, op["of_op_id"], qty_good=95, qty_scrap=5)
        close_of(conn, of_id)
        match_actuals_to_expected(conn, result.batch_id)

        absorptions = apply_cpm_absorption(conn, batch_id=result.batch_id)
        # Les déviations sur WS-1 utilisent la marge 1, les autres 1000
        ws1_abs = [a for a in absorptions
                   if conn.execute(
                       "SELECT e.workstation_id FROM expected_events e "
                       "JOIN event_deviations d ON d.expected_event_id = e.expected_event_id "
                       "WHERE d.deviation_id = ?",
                       (a.deviation_id,),
                   ).fetchone() and conn.execute(
                       "SELECT e.workstation_id FROM expected_events e "
                       "JOIN event_deviations d ON d.expected_event_id = e.expected_event_id "
                       "WHERE d.deviation_id = ?",
                       (a.deviation_id,),
                   ).fetchone()["workstation_id"] == "WS-1"
        ]
    # On a au moins une déviation WS-1 avec marge 1 min
    assert any(a.margin_minutes == 1.0 for a in ws1_abs)


def test_apply_is_idempotent(
    db_v3_with_deviations: tuple[Path, str]
) -> None:
    """Re-apply ne re-traite pas les déviations déjà absorbées."""
    db_path, batch_id = db_v3_with_deviations
    with db_session(db_path) as conn:
        first = apply_cpm_absorption(conn, batch_id=batch_id)
        second = apply_cpm_absorption(conn, batch_id=batch_id)
    assert len(first) > 0
    # 2e passe ne retouche pas (cpm_margin_used IS NOT NULL filtré)
    assert len(second) == 0
