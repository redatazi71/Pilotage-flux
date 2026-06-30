"""Hazards C.2 — couplage hazard → event_deviation → MACRS cell update."""

from __future__ import annotations

import pytest

from pilotage_flux.comparative.scenario import (
    HAZARD_BREAKDOWN,
    HAZARD_PO_DELAY,
    HAZARD_QUALITY_NC,
    HAZARD_URGENT_ORDER,
    HazardEvent,
)
from pilotage_flux.cybernetic.delta_engine.levels import (
    seed_default_delta_levels,
)
from pilotage_flux.cybernetic.macrs.couche2 import init_cells_from_layer1
from pilotage_flux.cybernetic.macrs.hazard_emission import (
    HAZARD_DEFAULT_IMPACT,
    HAZARD_TO_DEVIATION_KIND,
    emit_hazard,
    emit_hazards_batch,
)
from pilotage_flux.db import db_session


def _setup_bce(conn):
    init_cells_from_layer1(conn)
    seed_default_delta_levels(conn)


# ---------------------------------------------------------------------
# Constantes par défaut
# ---------------------------------------------------------------------

def test_default_impact_covers_all_five_hazards() -> None:
    assert set(HAZARD_DEFAULT_IMPACT) >= {
        HAZARD_BREAKDOWN, HAZARD_QUALITY_NC, HAZARD_PO_DELAY,
        HAZARD_URGENT_ORDER,
    }


def test_deviation_kinds_canonical() -> None:
    assert HAZARD_TO_DEVIATION_KIND[HAZARD_BREAKDOWN] == "time_delta"
    assert HAZARD_TO_DEVIATION_KIND[HAZARD_QUALITY_NC] == "qty_delta"
    assert HAZARD_TO_DEVIATION_KIND[HAZARD_PO_DELAY] == "time_delta"


# ---------------------------------------------------------------------
# emit_hazard — chemin nominal
# ---------------------------------------------------------------------

def test_emit_hazard_creates_event_deviation(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _setup_bce(conn)
        h = HazardEvent(
            day=3, kind=HAZARD_BREAKDOWN,
            payload={"workstation_id": "WS-2"},
        )
        res = emit_hazard(
            conn, h,
            occurred_at="2026-07-10T08:00:00",
            decided_at="2026-07-10T08:05:00",
        )
        assert res.deviation_id is not None
        # Vérifie la ligne event_deviations
        row = conn.execute(
            "SELECT deviation_kind, score, qualification "
            "FROM event_deviations WHERE deviation_id = ?",
            (res.deviation_id,),
        ).fetchone()
        assert row["deviation_kind"] == "time_delta"
        assert row["qualification"] == "auto"
        assert row["score"] == HAZARD_DEFAULT_IMPACT[HAZARD_BREAKDOWN]


def test_emit_hazard_uses_canonical_racine(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _setup_bce(conn)
        h = HazardEvent(day=3, kind=HAZARD_PO_DELAY,
                         payload={"po_id": "PO-1"})
        res = emit_hazard(
            conn, h,
            occurred_at="2026-07-10T08:00:00",
            decided_at="2026-07-10T08:05:00",
        )
        assert res.racine_id == "R011"
        assert res.categorie_code == "Mat"


def test_emit_hazard_alimente_macrs_cell(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _setup_bce(conn)
        h = HazardEvent(day=3, kind=HAZARD_BREAKDOWN, payload={})
        emit_hazard(
            conn, h,
            occurred_at="2026-07-10T08:00:00",
            decided_at="2026-07-10T08:05:00",
        )
        # La cellule R030/Op doit avoir n_events_total = 1
        row = conn.execute(
            "SELECT n_events_total, status FROM causal_cells "
            "WHERE racine_id='R030' AND categorie_code='Op'"
        ).fetchone()
        assert row["n_events_total"] == 1
        assert row["status"] in ("OBSERVING", "ACTIVE")


def test_emit_hazard_creates_delta_decision(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _setup_bce(conn)
        h = HazardEvent(day=3, kind=HAZARD_BREAKDOWN, payload={})
        res = emit_hazard(
            conn, h,
            occurred_at="2026-07-10T08:00:00",
            decided_at="2026-07-10T08:05:00",
        )
        assert res.cybernetic_decision is not None
        d = res.cybernetic_decision.delta_decision
        assert d.deviation_id == res.deviation_id
        assert d.racine_id == "R030"
        assert d.categorie_code == "Op"


# ---------------------------------------------------------------------
# emit_hazard — skip silencieux pour kind inconnu
# ---------------------------------------------------------------------

def test_emit_hazard_unknown_kind_skipped(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _setup_bce(conn)
        h = HazardEvent(day=3, kind="unknown_kind", payload={})
        res = emit_hazard(
            conn, h,
            occurred_at="2026-07-10T08:00:00",
            decided_at="2026-07-10T08:05:00",
        )
        assert res.skipped_reason == "unknown_racine"
        assert res.deviation_id is None
        assert res.cybernetic_decision is None
        # Aucune event_deviation créée
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM event_deviations"
        ).fetchone()
        assert row["n"] == 0


# ---------------------------------------------------------------------
# emit_hazard — score / délai customs depuis payload
# ---------------------------------------------------------------------

def test_emit_hazard_score_from_payload(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _setup_bce(conn)
        h = HazardEvent(
            day=3, kind=HAZARD_BREAKDOWN,
            payload={"impact_score": 0.95},
        )
        res = emit_hazard(
            conn, h,
            occurred_at="2026-07-10T08:00:00",
            decided_at="2026-07-10T08:05:00",
        )
        row = conn.execute(
            "SELECT score FROM event_deviations WHERE deviation_id = ?",
            (res.deviation_id,),
        ).fetchone()
        assert row["score"] == 0.95


def test_emit_hazard_score_clamped_to_unit_interval(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _setup_bce(conn)
        h = HazardEvent(
            day=3, kind=HAZARD_BREAKDOWN,
            payload={"impact_score": 2.5},   # > 1 → clamp à 1
        )
        res = emit_hazard(
            conn, h,
            occurred_at="2026-07-10T08:00:00",
            decided_at="2026-07-10T08:05:00",
        )
        row = conn.execute(
            "SELECT score FROM event_deviations WHERE deviation_id = ?",
            (res.deviation_id,),
        ).fetchone()
        assert row["score"] == 1.0


def test_emit_hazard_delta_value_from_payload(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _setup_bce(conn)
        h = HazardEvent(
            day=3, kind=HAZARD_PO_DELAY,
            payload={"delay_days": 3},   # → 3 × 1440 = 4320 min
        )
        res = emit_hazard(
            conn, h,
            occurred_at="2026-07-10T08:00:00",
            decided_at="2026-07-10T08:05:00",
        )
        row = conn.execute(
            "SELECT delta_value FROM event_deviations WHERE deviation_id = ?",
            (res.deviation_id,),
        ).fetchone()
        assert row["delta_value"] == 4320.0


def test_emit_hazard_uses_default_delay_hours(tmp_db) -> None:
    """Sans payload['delay_hours'], la cellule MACRS reçoit le délai
    par défaut HAZARD_DEFAULT_DELAY_HOURS[breakdown_ws] = 8h → b4_24h."""
    with db_session(tmp_db) as conn:
        _setup_bce(conn)
        h = HazardEvent(day=3, kind=HAZARD_BREAKDOWN, payload={})
        emit_hazard(
            conn, h,
            occurred_at="2026-07-10T08:00:00",
            decided_at="2026-07-10T08:05:00",
        )
        row = conn.execute(
            "SELECT delay_bin FROM causal_events"
        ).fetchone()
        assert row["delay_bin"] == "b4_24h"


def test_emit_hazard_delay_hours_override(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _setup_bce(conn)
        h = HazardEvent(
            day=3, kind=HAZARD_BREAKDOWN,
            payload={"delay_hours": 0.5},   # → b0_1h
        )
        emit_hazard(
            conn, h,
            occurred_at="2026-07-10T08:00:00",
            decided_at="2026-07-10T08:05:00",
        )
        row = conn.execute(
            "SELECT delay_bin FROM causal_events"
        ).fetchone()
        assert row["delay_bin"] == "b0_1h"


# ---------------------------------------------------------------------
# emit_hazards_batch
# ---------------------------------------------------------------------

def test_emit_hazards_batch_processes_all(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        _setup_bce(conn)
        hazards = [
            HazardEvent(day=2, kind=HAZARD_BREAKDOWN, payload={}),
            HazardEvent(day=4, kind=HAZARD_QUALITY_NC, payload={}),
            HazardEvent(day=6, kind=HAZARD_PO_DELAY, payload={}),
        ]
        results = emit_hazards_batch(
            conn, hazards,
            horizon_start_iso="2026-07-01",
        )
        assert len(results) == 3
        assert all(r.deviation_id is not None for r in results)
        # 3 déviations créées
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM event_deviations"
        ).fetchone()["n"]
        assert n == 3
        # 3 cellules MACRS différentes touchées
        cells = conn.execute(
            "SELECT racine_id FROM causal_cells WHERE n_events_total > 0"
        ).fetchall()
        racines = {c["racine_id"] for c in cells}
        assert racines == {"R030", "R039", "R011"}


def test_emit_hazards_batch_keeps_unknown_skipped(tmp_db) -> None:
    """Un hazard de kind inconnu dans le lot ne casse pas le batch."""
    with db_session(tmp_db) as conn:
        _setup_bce(conn)
        hazards = [
            HazardEvent(day=2, kind=HAZARD_BREAKDOWN, payload={}),
            HazardEvent(day=4, kind="phantom", payload={}),
            HazardEvent(day=6, kind=HAZARD_PO_DELAY, payload={}),
        ]
        results = emit_hazards_batch(
            conn, hazards, horizon_start_iso="2026-07-01",
        )
        assert results[0].deviation_id is not None
        assert results[1].skipped_reason == "unknown_racine"
        assert results[2].deviation_id is not None


def test_emit_hazards_batch_horodatages_aligned(tmp_db) -> None:
    """occurred_at = horizon_start + hazard.day jours."""
    with db_session(tmp_db) as conn:
        _setup_bce(conn)
        hazards = [
            HazardEvent(day=5, kind=HAZARD_BREAKDOWN, payload={}),
        ]
        results = emit_hazards_batch(
            conn, hazards, horizon_start_iso="2026-07-01",
        )
        # day=5 sur horizon 2026-07-01 → 2026-07-06
        assert results[0].deviation_id is not None
        row = conn.execute(
            "SELECT detected_at FROM event_deviations "
            "WHERE deviation_id = ?",
            (results[0].deviation_id,),
        ).fetchone()
        # ISO format avec time 00:00:00
        assert row["detected_at"].startswith("2026-07-06T00:00:00")


# ---------------------------------------------------------------------
# Intégration : hazard urgent → niveau élevé
# ---------------------------------------------------------------------

def test_emit_hazard_high_score_escalates_niveau(tmp_db) -> None:
    """Un hazard avec score=0.95 + freq=1 → score_combined ≈ 1.6 →
    replan_global L6 → enqueue approval_queue."""
    with db_session(tmp_db) as conn:
        _setup_bce(conn)
        h = HazardEvent(
            day=3, kind=HAZARD_BREAKDOWN,
            payload={"impact_score": 0.95},
        )
        res = emit_hazard(
            conn, h,
            occurred_at="2026-07-10T08:00:00",
            decided_at="2026-07-10T08:05:00",
        )
        cyber = res.cybernetic_decision
        assert cyber is not None
        assert cyber.final_niveau == "L6"
        assert cyber.approval_queue_id is not None
