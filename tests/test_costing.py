"""Tests du modèle de coûts (L7.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pilotage_flux.aps import compute_candidates
from pilotage_flux.costing import (
    DEFAULT_MOI_FIXED_PER_OF,
    DEFAULT_MOI_OVERHEAD_RATE,
    compute_of_cost,
    compute_run_cost_report,
    seed_default_unit_costs,
)
from pilotage_flux.db import db_session
from pilotage_flux.gates import run_p1_promotion
from pilotage_flux.importers import import_referentials
from pilotage_flux.mes import close_of, finish_operation, launch_of, start_operation


def _run_one_of_full_cycle(conn, of_id, qty_good, qty_scrap):
    launch_of(conn, of_id)
    ops = conn.execute(
        "SELECT of_op_id FROM order_operations WHERE of_id = ? "
        "ORDER BY sequence_idx",
        (of_id,),
    ).fetchall()
    for op in ops:
        start_operation(conn, op["of_op_id"])
        finish_operation(
            conn, op["of_op_id"], qty_good=qty_good, qty_scrap=qty_scrap
        )
    close_of(conn, of_id)


def test_seed_default_unit_costs(tmp_db: Path, fixtures_v1_dir: Path) -> None:
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        n = seed_default_unit_costs(conn)
        # 4 articles + 3 ws + 2 globaux = 9
        assert n == 9
        # Idempotent
        n2 = seed_default_unit_costs(conn)
        assert n2 == 0


def test_compute_of_cost_with_seeded_params(
    tmp_db: Path, fixtures_v1_dir: Path
) -> None:
    """Calcule un OF clôturé avec les prix seedés."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        seed_default_unit_costs(conn)
        compute_candidates(conn)
        run_p1_promotion(conn)
        of_id = conn.execute(
            "SELECT of_id FROM manufacturing_orders WHERE article_id = 'SEMI-1' "
            "ORDER BY of_id LIMIT 1"
        ).fetchone()["of_id"]
        # SEMI-1 quantité 100 : conso BOM 200 COMP-X (donc 200×2=400€), 1 op WS-1
        _run_one_of_full_cycle(conn, of_id, qty_good=95, qty_scrap=5)
        breakdown = compute_of_cost(conn, of_id)

    # Matière théorique : SEMI-1 utilise 2 COMP-X par unité × quantité
    # Quantité de cet OF varie selon la première SEMI-1 demandée — on vérifie
    # juste la cohérence.
    assert breakdown.material_cost > 0
    # MOD : durée op × rate WS-1 (35€/h). Durée min = écart actual_start/end ;
    # comme la sim est rapide, ~0 min. On vérifie juste >= 0.
    assert breakdown.mod_cost >= 0
    # MOI : fixed_per_of + overhead × mod
    expected_moi_min = DEFAULT_MOI_FIXED_PER_OF
    assert breakdown.moi_cost >= expected_moi_min - 0.01
    # Scrap : 5 × 8€ = 40€
    assert breakdown.scrap_cost == pytest.approx(40.0, rel=0.01)
    # Total > 0
    assert breakdown.total_cost > 0
    assert breakdown.cost_per_good_unit > 0
    # Pas d'article non valorisé (tout seedé)
    assert breakdown.unvalued_articles == []


def test_unvalued_articles_tracked(
    tmp_db: Path, fixtures_v1_dir: Path
) -> None:
    """Article sans unit_cost → tracé dans unvalued_articles, pas d'erreur."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        # Seede seulement WS rates, pas les unit_costs
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('workstation', 'WS-1', 'hourly_rate', 35.0)"
        )
        compute_candidates(conn)
        run_p1_promotion(conn)
        of_id = conn.execute(
            "SELECT of_id FROM manufacturing_orders LIMIT 1"
        ).fetchone()["of_id"]
        _run_one_of_full_cycle(conn, of_id, qty_good=100, qty_scrap=0)
        breakdown = compute_of_cost(conn, of_id)

    # Matière non valorisée
    assert breakdown.material_cost == 0.0
    assert len(breakdown.unvalued_articles) > 0


def test_run_cost_report_aggregation(
    tmp_db: Path, fixtures_v1_dir: Path
) -> None:
    """Agrégation de plusieurs OFs clôturés."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        seed_default_unit_costs(conn)
        compute_candidates(conn)
        run_p1_promotion(conn)
        of_ids = [
            r["of_id"] for r in conn.execute(
                "SELECT of_id FROM manufacturing_orders "
                "WHERE article_id = 'SEMI-1' ORDER BY of_id LIMIT 2"
            )
        ]
        for of_id in of_ids:
            _run_one_of_full_cycle(conn, of_id, qty_good=100, qty_scrap=0)
        report = compute_run_cost_report(conn)

    assert report.n_ofs >= 2
    assert report.grand_total > 0
    # Cohérence : grand_total = matière + MOD + MOI + scrap
    expected_total = (
        report.total_material + report.total_mod
        + report.total_moi + report.total_scrap
    )
    assert report.grand_total == pytest.approx(expected_total, rel=0.001)


def test_data_driven_cost_change(
    tmp_db: Path, fixtures_v1_dir: Path
) -> None:
    """Modifier un prix unitaire en base change le calcul (preuve data-driven)."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        seed_default_unit_costs(conn)
        compute_candidates(conn)
        run_p1_promotion(conn)
        of_id = conn.execute(
            "SELECT of_id FROM manufacturing_orders WHERE article_id = 'SEMI-1' "
            "LIMIT 1"
        ).fetchone()["of_id"]
        _run_one_of_full_cycle(conn, of_id, qty_good=100, qty_scrap=0)
        cost_before = compute_of_cost(conn, of_id).material_cost
        # Double le prix de COMP-X
        conn.execute(
            "UPDATE parameters SET valid_to = datetime('now') "
            "WHERE scope = 'article' AND scope_ref = 'COMP-X' "
            "AND name = 'unit_cost' AND valid_to IS NULL"
        )
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num, version) "
            "VALUES ('article', 'COMP-X', 'unit_cost', 4.0, 2)"
        )
        cost_after = compute_of_cost(conn, of_id).material_cost

    # COMP-X doublé → matière du SEMI-1 ~ doublée
    assert cost_after > cost_before * 1.5
