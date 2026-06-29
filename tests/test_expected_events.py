"""Tests de la génération des événements attendus depuis une tranche gelée (L3.1)."""

from pathlib import Path

import pytest

from pilotage_flux.aps import compute_candidates
from pilotage_flux.db import db_session
from pilotage_flux.events_v3 import (
    generate_expected_from_batch,
    list_expected,
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
from pilotage_flux.risk_debt import extinguish_risk_debt, list_risk_debts


@pytest.fixture
def db_frozen(
    tmp_db: Path, fixtures_v1_dir: Path
) -> tuple[Path, str, str]:
    """Base après P3 freeze - renvoie (db_path, contract_id, batch_id)."""
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
            conn,
            horizon_label="W27",
            horizon_start="2026-07-06",
            horizon_end="2026-07-12",
            candidate_ids=cids,
        )
        compute_coherence(conn, contract.contract_id)
        compute_smoothing(conn, contract.contract_id)
        for d in list_risk_debts(conn, status="open"):
            extinguish_risk_debt(conn, d.risk_debt_id, reason="test")
        result = run_p3_freeze(conn, contract.contract_id)
    return tmp_db, contract.contract_id, result.batch_id


def test_generate_creates_events_per_candidate(
    db_frozen: tuple[Path, str, str]
) -> None:
    """4 candidates × (op_starts + op_finishes + of_close)."""
    db_path, _, batch_id = db_frozen
    with db_session(db_path) as conn:
        events = generate_expected_from_batch(conn, batch_id)
    # ART-A : 2 ops × 2 events + 1 of_close = 5 events × 2 candidates = 10
    # SEMI-1 : 1 op × 2 events + 1 of_close = 3 events × 2 candidates = 6
    # Total = 16
    assert len(events) == 16


def test_generate_op_starts_and_finishes_paired(
    db_frozen: tuple[Path, str, str]
) -> None:
    """Pour chaque candidate, chaque op a un start ET un finish."""
    db_path, _, batch_id = db_frozen
    with db_session(db_path) as conn:
        generate_expected_from_batch(conn, batch_id)
        starts = list_expected(conn, batch_id=batch_id, event_type="op_start")
        finishes = list_expected(conn, batch_id=batch_id, event_type="op_finish")
    assert len(starts) == len(finishes)


def test_finish_after_start_chronologically(
    db_frozen: tuple[Path, str, str]
) -> None:
    """L'expected_at d'un op_finish est strictement après l'op_start de la même op."""
    db_path, _, batch_id = db_frozen
    with db_session(db_path) as conn:
        events = generate_expected_from_batch(conn, batch_id)
    # Pour chaque (candidate, seq), finish > start
    by_key: dict[tuple[str, int], dict[str, str]] = {}
    for e in events:
        if e.sequence_idx is not None:
            by_key.setdefault((e.candidate_id, e.sequence_idx), {})[e.event_type] = e.expected_at
    for key, pair in by_key.items():
        assert pair["op_finish"] > pair["op_start"], (
            f"finish {pair['op_finish']} <= start {pair['op_start']} for {key}"
        )


def test_of_close_after_last_op_finish(
    db_frozen: tuple[Path, str, str]
) -> None:
    db_path, _, batch_id = db_frozen
    with db_session(db_path) as conn:
        generate_expected_from_batch(conn, batch_id)
        for cid in ("CND-0001", "CND-0002", "CND-0003", "CND-0004"):
            evs = list_expected(conn, batch_id=batch_id, candidate_id=cid)
            of_close = [e for e in evs if e.event_type == "of_close"][0]
            last_finish = max(
                e.expected_at for e in evs if e.event_type == "op_finish"
            )
            assert of_close.expected_at >= last_finish


def test_op_duration_proportional_to_qty(
    db_frozen: tuple[Path, str, str]
) -> None:
    """L'écart op_finish - op_start = qty × unit_time_min."""
    from datetime import datetime
    db_path, _, batch_id = db_frozen
    with db_session(db_path) as conn:
        generate_expected_from_batch(conn, batch_id)
        # CND-0001 = ART-A qty 100 sur WS-2 (3.0 min) op 1
        evs = list_expected(conn, candidate_id="CND-0001", event_type="op_start")
        start_ws2 = next(e for e in evs if e.workstation_id == "WS-2")
        evs_f = list_expected(conn, candidate_id="CND-0001", event_type="op_finish")
        finish_ws2 = next(e for e in evs_f if e.workstation_id == "WS-2")
    start_dt = datetime.fromisoformat(start_ws2.expected_at)
    finish_dt = datetime.fromisoformat(finish_ws2.expected_at)
    delta_min = (finish_dt - start_dt).total_seconds() / 60
    # qty 100 × 3.0 = 300 min
    assert delta_min == 300


def test_generate_is_idempotent(db_frozen: tuple[Path, str, str]) -> None:
    """Re-génération purge et regénère ; le compte reste constant."""
    db_path, _, batch_id = db_frozen
    with db_session(db_path) as conn:
        first = generate_expected_from_batch(conn, batch_id)
        second = generate_expected_from_batch(conn, batch_id)
    assert len(first) == len(second) == 16


def test_generate_refuses_unknown_batch(db_frozen: tuple[Path, str, str]) -> None:
    db_path, _, _ = db_frozen
    with db_session(db_path) as conn:
        with pytest.raises(ValueError, match="inconnue"):
            generate_expected_from_batch(conn, "FZ-INEXISTANT")


def test_list_unmatched_only_initially_returns_all(
    db_frozen: tuple[Path, str, str]
) -> None:
    """Au tout début, aucun event n'a son matched_actual_id - tous sont unmatched."""
    db_path, _, batch_id = db_frozen
    with db_session(db_path) as conn:
        generate_expected_from_batch(conn, batch_id)
        all_evs = list_expected(conn, batch_id=batch_id)
        unmatched = list_expected(conn, batch_id=batch_id, unmatched_only=True)
    assert len(unmatched) == len(all_evs) == 16
