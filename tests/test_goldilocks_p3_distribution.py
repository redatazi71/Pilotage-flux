"""Goldilocks #5 — Distribution de contrats en sortie P3 → PCs au grain op."""

from __future__ import annotations

import pytest

from pilotage_flux.cybernetic.p3_distribution import (
    distribute_contracts_at_p3_exit,
    get_pcs_for_batch,
    get_pcs_for_contract,
)
from pilotage_flux.cybernetic.production_contract import get_pc
from pilotage_flux.db import db_session


def _seed_chain(conn, *, n_ops=2, quantity=100.0):
    """Seed minimal : 1 SO → 1 candidate → 1 OF (2 ops) → 1 contract
    (1 version, 1 link sur le candidate) → 1 freeze_batch.

    Renvoie (batch_id, contract_id, of_id, candidate_id).
    """
    conn.execute(
        "INSERT INTO articles (article_id, label) VALUES ('ART', 'T')"
    )
    for i in range(n_ops):
        conn.execute(
            "INSERT INTO workstations "
            "(workstation_id, label, sequence_idx) VALUES (?, 'T', ?)",
            (f"WS-{i+1}", i + 1),
        )
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('workstation', ?, 'hourly_rate', 60.0)",
            (f"WS-{i+1}",),
        )
    conn.execute(
        "INSERT INTO sales_orders "
        "(sales_order_id, article_id, quantity, due_date) "
        "VALUES ('SO-1', 'ART', ?, '2026-12-01')",
        (quantity,),
    )
    conn.execute(
        "INSERT INTO candidate_orders "
        "(candidate_id, sales_order_id, article_id, quantity, status, zone) "
        "VALUES ('CAND-1', 'SO-1', 'ART', ?, 'promoted', 'gelee')",
        (quantity,),
    )
    conn.execute(
        "INSERT INTO manufacturing_orders "
        "(of_id, candidate_id, article_id, quantity, status) "
        "VALUES ('OF-1', 'CAND-1', 'ART', ?, 'launched')",
        (quantity,),
    )
    for i in range(n_ops):
        conn.execute(
            "INSERT INTO order_operations "
            "(of_op_id, of_id, sequence_idx, workstation_id, unit_time_min, "
            " status) VALUES (?, 'OF-1', ?, ?, 2.0, 'planned')",
            (100 + i, i + 1, f"WS-{i+1}"),
        )
    conn.execute(
        "INSERT INTO flux_contracts "
        "(contract_id, horizon_label, horizon_start, horizon_end, "
        " status, current_version) "
        "VALUES ('FX-1', 'W27', '2026-07-01', '2026-07-08', 'frozen', 1)"
    )
    conn.execute(
        "INSERT INTO flux_contract_versions "
        "(contract_id, version, total_quantity, is_coherent) "
        "VALUES ('FX-1', 1, ?, 1)",
        (quantity,),
    )
    conn.execute(
        "INSERT INTO flux_contract_links "
        "(contract_id, version, candidate_id, qty_in_contract, sequence_idx) "
        "VALUES ('FX-1', 1, 'CAND-1', ?, 1)",
        (quantity,),
    )
    conn.execute(
        "INSERT INTO freeze_batches "
        "(batch_id, horizon_start, horizon_end, status, decision, "
        " total_quantity, contract_count, candidate_count) "
        "VALUES ('FZ-1', '2026-07-01', '2026-07-08', 'frozen', "
        " 'FREEZE', ?, 1, 1)",
        (quantity,),
    )
    conn.execute(
        "INSERT INTO freeze_batch_contracts "
        "(batch_id, contract_id, version) VALUES ('FZ-1', 'FX-1', 1)"
    )
    return "FZ-1", "FX-1", "OF-1", "CAND-1"


def test_distribute_creates_one_pc_per_operation(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        batch_id, contract_id, of_id, _ = _seed_chain(conn, n_ops=2)
        res = distribute_contracts_at_p3_exit(conn, batch_id)
        assert res.batch_id == "FZ-1"
        assert res.n_pcs == 2
        assert res.n_ofs == 1
        assert res.contracts_processed == ("FX-1",)
        assert res.ofs_processed == ("OF-1",)
        # PCs créés ont bien origin = flux_contract / FX-1
        for pc_id in res.pcs_created:
            row = get_pc(conn, pc_id)
            assert row["origin_kind"] == "flux_contract"
            assert row["origin_ref"] == "FX-1"


def test_distribute_unknown_batch_raises(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        with pytest.raises(ValueError, match="inexistant"):
            distribute_contracts_at_p3_exit(conn, "FZ-XX")


def test_distribute_is_idempotent(tmp_db) -> None:
    """Re-runner la distribution ne duplique pas les PCs."""
    with db_session(tmp_db) as conn:
        batch_id, _, _, _ = _seed_chain(conn, n_ops=2)
        res1 = distribute_contracts_at_p3_exit(conn, batch_id)
        res2 = distribute_contracts_at_p3_exit(conn, batch_id)
        assert res1.n_pcs == 2
        assert res2.n_pcs == 0  # déjà couvert
        # Total PCs en base = 2
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM production_contracts"
        ).fetchone()
        assert total["n"] == 2


def test_distribute_signals_candidate_without_of(tmp_db) -> None:
    """Si un candidate du contrat n'a pas d'OF, il est listé."""
    with db_session(tmp_db) as conn:
        _seed_chain(conn, n_ops=1)
        # Ajoute un 2e candidate dans le même contract sans OF
        conn.execute(
            "INSERT INTO candidate_orders "
            "(candidate_id, article_id, quantity, status, zone) "
            "VALUES ('CAND-2', 'ART', 10, 'candidate', 'libre')"
        )
        conn.execute(
            "INSERT INTO flux_contract_links "
            "(contract_id, version, candidate_id, qty_in_contract, "
            " sequence_idx) "
            "VALUES ('FX-1', 1, 'CAND-2', 10, 2)"
        )
        res = distribute_contracts_at_p3_exit(conn, "FZ-1")
        assert "CAND-2" in res.candidates_without_of
        # CAND-1 a un OF → 1 PC ; CAND-2 non → ignoré sans erreur
        assert res.n_pcs == 1


def test_get_pcs_for_batch_returns_distributed(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        batch_id, _, _, _ = _seed_chain(conn, n_ops=2)
        distribute_contracts_at_p3_exit(conn, batch_id)
        pcs = get_pcs_for_batch(conn, batch_id)
        assert len(pcs) == 2


def test_get_pcs_for_contract_returns_distributed(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _seed_chain(conn, n_ops=2)
        distribute_contracts_at_p3_exit(conn, "FZ-1")
        pcs = get_pcs_for_contract(conn, "FX-1")
        assert len(pcs) == 2


def test_distribute_handles_multiple_contracts_in_batch(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _seed_chain(conn, n_ops=1)
        # Ajoute un 2e contrat / candidate / OF dans le même batch
        conn.execute(
            "INSERT INTO candidate_orders "
            "(candidate_id, article_id, quantity, status, zone) "
            "VALUES ('CAND-2', 'ART', 50, 'promoted', 'gelee')"
        )
        conn.execute(
            "INSERT INTO manufacturing_orders "
            "(of_id, candidate_id, article_id, quantity, status) "
            "VALUES ('OF-2', 'CAND-2', 'ART', 50, 'launched')"
        )
        conn.execute(
            "INSERT INTO order_operations "
            "(of_op_id, of_id, sequence_idx, workstation_id, unit_time_min, "
            " status) "
            "VALUES (200, 'OF-2', 1, 'WS-1', 2.0, 'planned')"
        )
        conn.execute(
            "INSERT INTO flux_contracts "
            "(contract_id, horizon_label, horizon_start, horizon_end, "
            " status, current_version) "
            "VALUES ('FX-2', 'W27', '2026-07-01', '2026-07-08', 'frozen', 1)"
        )
        conn.execute(
            "INSERT INTO flux_contract_versions "
            "(contract_id, version, total_quantity, is_coherent) "
            "VALUES ('FX-2', 1, 50, 1)"
        )
        conn.execute(
            "INSERT INTO flux_contract_links "
            "(contract_id, version, candidate_id, qty_in_contract, "
            " sequence_idx) "
            "VALUES ('FX-2', 1, 'CAND-2', 50, 1)"
        )
        conn.execute(
            "INSERT INTO freeze_batch_contracts "
            "(batch_id, contract_id, version) VALUES ('FZ-1', 'FX-2', 1)"
        )
        res = distribute_contracts_at_p3_exit(conn, "FZ-1")
        assert set(res.contracts_processed) == {"FX-1", "FX-2"}
        assert set(res.ofs_processed) == {"OF-1", "OF-2"}
        assert res.n_pcs == 2


def test_distribute_pc_targets_use_op_routing(tmp_db) -> None:
    """Cible T = unit_time × quantity, C = T × hourly_rate / 60."""
    with db_session(tmp_db) as conn:
        _seed_chain(conn, n_ops=1, quantity=100.0)
        res = distribute_contracts_at_p3_exit(conn, "FZ-1")
        pc = get_pc(conn, res.pcs_created[0])
        # unit_time = 2.0, qty = 100 → T = 200 min
        assert pc["target_time_min"] == 200.0
        # hourly_rate = 60 €/h → C = 200 × 60 / 60 = 200
        assert pc["target_cost"] == 200.0
        # Er = 100
        assert pc["target_qty_good"] == 100.0
