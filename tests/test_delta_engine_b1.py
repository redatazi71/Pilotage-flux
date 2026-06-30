"""Moteur Delta B.1 — niveaux d'action unifiés + delta_decisions.

Couvre :
  - 6 niveaux canoniques (L1..L6) avec mapping cadrage 4 niveaux
  - Subsidiarité humaine (requires_human sur L4/L5/L6)
  - Seed idempotent
  - CRUD delta_decisions + cycle de vie (pending → executed/rejected)
  - Compteurs par niveau et par niveau cadrage
"""

from __future__ import annotations

import pytest

from pilotage_flux.cybernetic.delta_engine import (
    L_CORRIGER_LOCAL,
    L_ESCALADER,
    L_INFORMER,
    L_REPLANIFIER_GLOBAL,
    L_REPLANIFIER_LOCAL,
    L_SURVEILLER,
    NIVEAUX_ORDRE,
    STATUS_EXECUTED,
    STATUS_PENDING,
    STATUS_REJECTED,
    count_decisions_by_cadrage_level,
    count_decisions_by_level,
    create_delta_decision,
    get_decision,
    get_delta_level,
    list_decisions_for_deviation,
    list_delta_levels,
    list_levels_for_cadrage,
    mark_decision_executed,
    mark_decision_expired,
    mark_decision_rejected,
    seed_default_delta_levels,
)
from pilotage_flux.db import db_session


# ---------------------------------------------------------------------
# Niveaux
# ---------------------------------------------------------------------

def test_niveaux_ordre_canonical() -> None:
    assert NIVEAUX_ORDRE == (
        L_INFORMER, L_SURVEILLER, L_CORRIGER_LOCAL,
        L_REPLANIFIER_LOCAL, L_ESCALADER, L_REPLANIFIER_GLOBAL,
    )


def test_seed_inserts_six_levels(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        n = seed_default_delta_levels(conn)
        assert n == 6
        levels = list_delta_levels(conn)
        assert [lv.niveau_code for lv in levels] == list(NIVEAUX_ORDRE)


def test_seed_is_idempotent(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        first = seed_default_delta_levels(conn)
        second = seed_default_delta_levels(conn)
        assert first == 6
        assert second == 0


def test_levels_carry_cadrage_mapping(tmp_db) -> None:
    """L1, L2 → N1 ; L3 → N2 ; L4, L5 → N3 ; L6 → N4."""
    with db_session(tmp_db) as conn:
        seed_default_delta_levels(conn)
        mapping = {
            lv.niveau_code: lv.cadrage_level
            for lv in list_delta_levels(conn)
        }
        assert mapping["L1"] == 1
        assert mapping["L2"] == 1
        assert mapping["L3"] == 2
        assert mapping["L4"] == 3
        assert mapping["L5"] == 3
        assert mapping["L6"] == 4


def test_levels_carry_requires_human_flag(tmp_db) -> None:
    """Subsidiarité humaine : L1/L2/L3 = auto, L4/L5/L6 = humain."""
    with db_session(tmp_db) as conn:
        seed_default_delta_levels(conn)
        flags = {
            lv.niveau_code: lv.requires_human
            for lv in list_delta_levels(conn)
        }
        assert flags["L1"] is False
        assert flags["L2"] is False
        assert flags["L3"] is False
        assert flags["L4"] is True
        assert flags["L5"] is True
        assert flags["L6"] is True


def test_levels_scope_canonical(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        seed_default_delta_levels(conn)
        scopes = {lv.niveau_code: lv.scope
                   for lv in list_delta_levels(conn)}
        assert scopes["L1"] == "none"
        assert scopes["L2"] == "none"
        assert scopes["L3"] == "local"
        assert scopes["L4"] == "local"
        assert scopes["L5"] == "local"
        assert scopes["L6"] == "global"


def test_list_levels_for_cadrage(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        seed_default_delta_levels(conn)
        n1 = list_levels_for_cadrage(conn, 1)
        assert [lv.niveau_code for lv in n1] == ["L1", "L2"]
        n3 = list_levels_for_cadrage(conn, 3)
        assert [lv.niveau_code for lv in n3] == ["L4", "L5"]


def test_get_delta_level_unknown_returns_none(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        seed_default_delta_levels(conn)
        assert get_delta_level(conn, "L9") is None


# ---------------------------------------------------------------------
# Décisions
# ---------------------------------------------------------------------

def test_create_decision_basic(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        seed_default_delta_levels(conn)
        d_id = create_delta_decision(
            conn,
            niveau_code=L_CORRIGER_LOCAL,
            decided_at="2026-07-15T10:00:00",
            score_magnitude=0.45,
            frequency=0.20,
            explanation="ajustement marge",
            actor="auto:delta_engine",
        )
        decision = get_decision(conn, d_id)
        assert decision is not None
        assert decision.niveau_code == L_CORRIGER_LOCAL
        assert decision.status == STATUS_PENDING
        assert decision.score_magnitude == 0.45
        assert decision.frequency == 0.20
        assert decision.actor == "auto:delta_engine"


def test_create_decision_rejects_unknown_niveau(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        seed_default_delta_levels(conn)
        with pytest.raises(ValueError, match="invalide"):
            create_delta_decision(
                conn, niveau_code="L7",
                decided_at="2026-07-15T10:00:00",
            )


def test_create_decision_requires_seeded_levels(tmp_db) -> None:
    """Sans seed préalable, on lève (les FKs sont strictes)."""
    with db_session(tmp_db) as conn:
        with pytest.raises(ValueError, match="seedé"):
            create_delta_decision(
                conn, niveau_code=L_INFORMER,
                decided_at="2026-07-15T10:00:00",
            )


def test_mark_decision_executed(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        seed_default_delta_levels(conn)
        d_id = create_delta_decision(
            conn, niveau_code=L_INFORMER,
            decided_at="2026-07-15T10:00:00",
        )
        mark_decision_executed(
            conn, d_id,
            executed_at="2026-07-15T10:30:00",
            actor="auto:executor",
        )
        d = get_decision(conn, d_id)
        assert d.status == STATUS_EXECUTED
        assert d.executed_at == "2026-07-15T10:30:00"
        assert d.actor == "auto:executor"


def test_mark_decision_rejected(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        seed_default_delta_levels(conn)
        d_id = create_delta_decision(
            conn, niveau_code=L_REPLANIFIER_LOCAL,
            decided_at="2026-07-15T10:00:00",
        )
        mark_decision_rejected(
            conn, d_id, actor="human:planner",
            explanation="risque trop élevé",
        )
        d = get_decision(conn, d_id)
        assert d.status == STATUS_REJECTED
        assert d.actor == "human:planner"
        assert d.explanation == "risque trop élevé"


def test_mark_decision_expired(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        seed_default_delta_levels(conn)
        d_id = create_delta_decision(
            conn, niveau_code=L_ESCALADER,
            decided_at="2026-07-15T10:00:00",
        )
        mark_decision_expired(conn, d_id)
        d = get_decision(conn, d_id)
        assert d.status == "expired"


def test_list_decisions_for_deviation_ordered(tmp_db) -> None:
    """Insère event_deviation réelle + 2 décisions sur celle-ci."""
    with db_session(tmp_db) as conn:
        seed_default_delta_levels(conn)
        cur = conn.execute(
            "INSERT INTO event_deviations "
            "(deviation_kind, delta_value, score, qualification, "
            " detected_at) "
            "VALUES ('time_delta', 5.0, 0.5, 'mineur', "
            "        '2026-07-15T08:00:00')"
        )
        dev_id = int(cur.lastrowid)
        d1 = create_delta_decision(
            conn, niveau_code=L_SURVEILLER,
            decided_at="2026-07-15T08:00:00",
            deviation_id=dev_id,
        )
        d2 = create_delta_decision(
            conn, niveau_code=L_CORRIGER_LOCAL,
            decided_at="2026-07-15T09:00:00",
            deviation_id=dev_id,
        )
        decisions = list_decisions_for_deviation(conn, dev_id)
        assert [d.delta_decision_id for d in decisions] == [d1, d2]


def test_count_decisions_by_level(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        seed_default_delta_levels(conn)
        for niveau in (L_INFORMER, L_INFORMER, L_CORRIGER_LOCAL):
            create_delta_decision(
                conn, niveau_code=niveau,
                decided_at="2026-07-15T08:00:00",
            )
        counts = count_decisions_by_level(conn)
        assert counts == {L_INFORMER: 2, L_CORRIGER_LOCAL: 1}


def test_count_decisions_by_cadrage_level(tmp_db) -> None:
    """N1 = L1+L2 ; N2 = L3 ; N3 = L4+L5 ; N4 = L6."""
    with db_session(tmp_db) as conn:
        seed_default_delta_levels(conn)
        # 2 L1 + 1 L2 + 3 L3 + 1 L4 + 0 L5 + 1 L6
        for niveau in ([L_INFORMER] * 2 + [L_SURVEILLER] * 1 +
                        [L_CORRIGER_LOCAL] * 3 +
                        [L_REPLANIFIER_LOCAL] * 1 +
                        [L_REPLANIFIER_GLOBAL] * 1):
            create_delta_decision(
                conn, niveau_code=niveau,
                decided_at="2026-07-15T08:00:00",
            )
        counts = count_decisions_by_cadrage_level(conn)
        # N1 = 2+1 = 3 ; N2 = 3 ; N3 = 1+0 = 1 ; N4 = 1
        assert counts == {1: 3, 2: 3, 3: 1, 4: 1}


def test_count_decisions_filter_by_status(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        seed_default_delta_levels(conn)
        d1 = create_delta_decision(
            conn, niveau_code=L_INFORMER,
            decided_at="2026-07-15T08:00:00",
        )
        create_delta_decision(
            conn, niveau_code=L_INFORMER,
            decided_at="2026-07-15T09:00:00",
        )
        mark_decision_executed(
            conn, d1, executed_at="2026-07-15T08:30:00",
        )
        only_pending = count_decisions_by_level(conn, status=STATUS_PENDING)
        only_executed = count_decisions_by_level(conn, status=STATUS_EXECUTED)
        assert only_pending == {L_INFORMER: 1}
        assert only_executed == {L_INFORMER: 1}


def test_decision_stores_macrs_attribution(tmp_db) -> None:
    """racine_id + categorie_code peuvent être attachés (pour wiring
    avec MACRS Couche 2 en B.3)."""
    with db_session(tmp_db) as conn:
        seed_default_delta_levels(conn)
        # Sans seed MACRS, la FK est permissive (NULL OK).
        d_id = create_delta_decision(
            conn, niveau_code=L_CORRIGER_LOCAL,
            decided_at="2026-07-15T10:00:00",
            racine_id=None,
            categorie_code=None,
        )
        d = get_decision(conn, d_id)
        assert d.racine_id is None
        assert d.categorie_code is None
