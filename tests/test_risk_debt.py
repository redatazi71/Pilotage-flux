"""Tests du registre risk_debt."""

from datetime import date, timedelta
from pathlib import Path

import pytest

from pilotage_flux.aps import compute_candidates
from pilotage_flux.db import db_session
from pilotage_flux.importers import import_referentials
from pilotage_flux.risk_debt import (
    expire_overdue_risk_debts,
    extinguish_risk_debt,
    has_open_debt,
    list_risk_debts,
    open_risk_debt,
)


@pytest.fixture
def db_v1(tmp_db: Path, fixtures_v1_dir: Path) -> Path:
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        compute_candidates(conn)
    return tmp_db


def _first_candidate(db_path: Path) -> str:
    with db_session(db_path) as conn:
        return conn.execute(
            "SELECT candidate_id FROM candidate_orders LIMIT 1"
        ).fetchone()["candidate_id"]


def test_open_risk_debt_creates_record(db_v1: Path) -> None:
    cid = _first_candidate(db_v1)
    with db_session(db_v1) as conn:
        d = open_risk_debt(
            conn,
            candidate_id=cid,
            criterion="bottleneck_capacity",
            rule_id="R-P2-04",
            score=0.72,
            explanation="surcharge WS-2",
        )
    assert d.candidate_id == cid
    assert d.status == "open"
    assert d.score == 0.72
    assert d.deadline is not None  # default lu depuis parameters
    assert d.extinguished_at is None


def test_default_deadline_is_data_driven(db_v1: Path) -> None:
    """La deadline par defaut est lue depuis parameters (risk_debt_default_deadline_days)."""
    cid = _first_candidate(db_v1)
    with db_session(db_v1) as conn:
        conn.execute(
            "UPDATE parameters SET value_num = 3 "
            "WHERE scope = 'global' AND name = 'risk_debt_default_deadline_days'"
        )
        d = open_risk_debt(
            conn,
            candidate_id=cid,
            criterion="components_projectable",
            rule_id="R-P2-05",
            score=0.3,
        )
    expected = (date.today() + timedelta(days=3)).isoformat()
    assert d.deadline == expected


def test_extinguish_open_risk_debt(db_v1: Path) -> None:
    cid = _first_candidate(db_v1)
    with db_session(db_v1) as conn:
        d = open_risk_debt(
            conn,
            candidate_id=cid,
            criterion="components_projectable",
            rule_id="R-P2-05",
            score=0.3,
        )
        result = extinguish_risk_debt(conn, d.risk_debt_id, reason="composants reçus")
        debts = list_risk_debts(conn, candidate_id=cid)
    assert result.status == "extinct"
    assert result.extinguished_at is not None
    assert result.extinction_reason == "composants reçus"
    assert debts[0].status == "extinct"


def test_cannot_extinguish_already_extinct(db_v1: Path) -> None:
    cid = _first_candidate(db_v1)
    with db_session(db_v1) as conn:
        d = open_risk_debt(
            conn, candidate_id=cid, criterion="x", rule_id="R-X", score=0.5
        )
        extinguish_risk_debt(conn, d.risk_debt_id, reason="ok")
        with pytest.raises(ValueError, match="attendu 'open'"):
            extinguish_risk_debt(conn, d.risk_debt_id, reason="encore")


def test_expire_overdue_passes_only_overdue_to_expired(db_v1: Path) -> None:
    cid = _first_candidate(db_v1)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    with db_session(db_v1) as conn:
        d_old = open_risk_debt(
            conn, candidate_id=cid, criterion="x", rule_id="R-X",
            score=0.5, deadline=yesterday,
        )
        d_new = open_risk_debt(
            conn, candidate_id=cid, criterion="x", rule_id="R-X",
            score=0.5, deadline=tomorrow,
        )
        n = expire_overdue_risk_debts(conn)

        old_status = conn.execute(
            "SELECT status FROM risk_debt_register WHERE risk_debt_id = ?",
            (d_old.risk_debt_id,),
        ).fetchone()["status"]
        new_status = conn.execute(
            "SELECT status FROM risk_debt_register WHERE risk_debt_id = ?",
            (d_new.risk_debt_id,),
        ).fetchone()["status"]
    assert n == 1
    assert old_status == "expired"
    assert new_status == "open"


def test_has_open_debt_returns_correctly(db_v1: Path) -> None:
    cid = _first_candidate(db_v1)
    with db_session(db_v1) as conn:
        assert has_open_debt(conn, cid) is False
        open_risk_debt(
            conn, candidate_id=cid, criterion="x", rule_id="R-X", score=0.5
        )
        assert has_open_debt(conn, cid) is True


def test_list_risk_debts_filters_by_status(db_v1: Path) -> None:
    cid = _first_candidate(db_v1)
    with db_session(db_v1) as conn:
        d1 = open_risk_debt(
            conn, candidate_id=cid, criterion="x", rule_id="R-X", score=0.5
        )
        d2 = open_risk_debt(
            conn, candidate_id=cid, criterion="y", rule_id="R-Y", score=0.4
        )
        extinguish_risk_debt(conn, d1.risk_debt_id, reason="resolved")
        open_only = list_risk_debts(conn, status="open")
        extinct_only = list_risk_debts(conn, status="extinct")
    assert len(open_only) == 1
    assert open_only[0].risk_debt_id == d2.risk_debt_id
    assert len(extinct_only) == 1
    assert extinct_only[0].risk_debt_id == d1.risk_debt_id
