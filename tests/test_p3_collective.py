"""Tests P3 collective (L6.1) — cohérence multi-contrats sur même horizon."""

from __future__ import annotations

from pathlib import Path

import pytest

from pilotage_flux.aps import compute_candidates
from pilotage_flux.db import db_session
from pilotage_flux.flux import (
    compute_coherence,
    compute_smoothing,
    create_contract,
)
from pilotage_flux.gates import (
    DECISION_DEFER_ALL,
    DECISION_FREEZE_ALL,
    DECISION_PARTIAL_FREEZE,
    evaluate_p3_collective,
    run_p2_on_libre_zone,
    run_p3_collective_freeze,
)
from pilotage_flux.importers import import_referentials
from pilotage_flux.risk_debt import extinguish_risk_debt, list_risk_debts
from pilotage_flux.zones import create_cycle, open_cycle


def _setup_two_contracts(conn) -> list[str]:
    """Crée 2 contrats hebdo couvrant la même semaine, avec candidates répartis."""
    compute_candidates(conn)
    run_p2_on_libre_zone(conn)
    cids = [
        r["candidate_id"]
        for r in conn.execute(
            "SELECT candidate_id FROM candidate_orders "
            "WHERE zone = 'negociable' ORDER BY candidate_id ASC"
        ).fetchall()
    ]
    assert len(cids) >= 2, "Pré-requis : au moins 2 candidates négociables"
    # Split en 2 contrats sur le même horizon
    half = len(cids) // 2
    c1 = create_contract(
        conn, horizon_label="W27-A",
        horizon_start="2026-07-06", horizon_end="2026-07-12",
        candidate_ids=cids[:half],
    )
    c2 = create_contract(
        conn, horizon_label="W27-B",
        horizon_start="2026-07-06", horizon_end="2026-07-12",
        candidate_ids=cids[half:],
    )
    for c in (c1, c2):
        compute_coherence(conn, c.contract_id)
        compute_smoothing(conn, c.contract_id)
    for d in list_risk_debts(conn, status="open"):
        extinguish_risk_debt(conn, d.risk_debt_id, reason="test")
    return [c1.contract_id, c2.contract_id]


def test_p3_collective_freeze_all_under_capacity(
    tmp_db: Path, fixtures_v1_dir: Path
) -> None:
    """Deux contrats avec charge totale sous capacité → FREEZE_ALL."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        cids = _setup_two_contracts(conn)

        result = run_p3_collective_freeze(conn, cids)

    assert result.decision == DECISION_FREEZE_ALL
    assert set(result.frozen_contracts) == set(cids)
    assert result.deferred_contracts == []
    assert result.batch_id is not None
    assert result.bottleneck_workstation is not None
    assert result.bottleneck_load <= result.bottleneck_capacity


def test_p3_collective_partial_freeze_when_overloaded(
    tmp_db: Path, fixtures_v1_dir: Path
) -> None:
    """Capacité réduite drastiquement → PARTIAL_FREEZE."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        cids = _setup_two_contracts(conn)
        # Réduit drastiquement la capacité du poste WS-3 (goulot ART-A)
        conn.execute(
            """
            UPDATE parameters
            SET valid_to = datetime('now')
            WHERE scope = 'workstation' AND scope_ref = 'WS-3'
              AND name = 'capacity_factor' AND valid_to IS NULL
            """,
        )
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num, version) "
            "VALUES ('workstation', 'WS-3', 'capacity_factor', 0.05, 2)"
        )

        result = run_p3_collective_freeze(conn, cids)

    # Doit être PARTIAL (un seul rentre) ou DEFER (aucun rentre)
    assert result.decision in (DECISION_PARTIAL_FREEZE, DECISION_DEFER_ALL)
    if result.decision == DECISION_PARTIAL_FREEZE:
        assert len(result.frozen_contracts) >= 1
        assert len(result.deferred_contracts) >= 1
        assert result.bottleneck_load > result.bottleneck_capacity


def test_p3_collective_traces_event_and_decision(
    tmp_db: Path, fixtures_v1_dir: Path
) -> None:
    """L'événement GATE_DECISION P3_COLLECTIVE et la gate_decisions_v1 sont tracés."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        cids = _setup_two_contracts(conn)
        cycle = create_cycle(
            conn, gate="P3", period_start="2026-07-06",
            period_end="2026-07-12", cadence_days=7,
            cycle_id="P3-2026-W27",
        )
        open_cycle(conn, cycle.cycle_id)
        result = run_p3_collective_freeze(conn, cids, cycle_id="P3-2026-W27")

        # gate_decisions_v1 doit avoir une ligne P3_COLLECTIVE
        row = conn.execute(
            "SELECT * FROM gate_decisions_v1 WHERE gate = 'P3_COLLECTIVE'"
        ).fetchone()
        assert row is not None
        assert row["decision"] in (
            DECISION_FREEZE_ALL, DECISION_PARTIAL_FREEZE, DECISION_DEFER_ALL,
        )
        assert row["cycle_id"] == "P3-2026-W27"

        # event_store doit avoir un GATE_DECISION P3_COLLECTIVE
        ev = conn.execute(
            "SELECT * FROM event_store "
            "WHERE event_type = 'GATE_DECISION' "
            "AND aggregate_type = 'flux_contract_group'"
        ).fetchone()
        assert ev is not None
        assert "P3_COLLECTIVE" in ev["payload_json"]


def test_evaluate_p3_collective_pure_function(
    tmp_db: Path, fixtures_v1_dir: Path
) -> None:
    """evaluate_p3_collective ne modifie pas l'état."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        cids = _setup_two_contracts(conn)

        before_frozen = conn.execute(
            "SELECT COUNT(*) AS n FROM freeze_batches"
        ).fetchone()["n"]
        per_contract, profiles, bottleneck, cumul_load, capacity = (
            evaluate_p3_collective(conn, cids)
        )
        after_frozen = conn.execute(
            "SELECT COUNT(*) AS n FROM freeze_batches"
        ).fetchone()["n"]

    assert before_frozen == after_frozen
    assert len(per_contract) == 2
    assert len(profiles) == 2
    assert bottleneck  # un goulot identifié
    assert capacity > 0
