"""MACRS A.2 — Tests Couche 2 : causal_cells + lifecycle 4 statuts.

Couvre :
  - Initialisation depuis Couche 1 → 165 cellules INCOMING
  - Transition INCOMING → OBSERVING au 1er événement
  - Transition OBSERVING → ACTIVE quand K du sous-domaine atteint
  - Activation synchrone : toutes les cellules d'un sous-domaine
    basculent en ACTIVE en bloc
  - K paramétrable par sous-domaine
  - Compteurs et timestamps
  - Refus d'enregistrer un événement sur couple inactif
"""

from __future__ import annotations

import pytest

from pilotage_flux.cybernetic.macrs.couche1 import seed_macrs_layer1
from pilotage_flux.cybernetic.macrs.couche2 import (
    K_DEFAULT,
    STATUS_ACTIVE,
    STATUS_INCOMING,
    STATUS_OBSERVING,
    STATUSES,
    count_cells_by_status,
    get_cell,
    get_k_for_subdomain,
    init_cells_from_layer1,
    list_cells_by_status,
    record_event,
)
from pilotage_flux.db import db_session


def test_statuses_canonical() -> None:
    assert STATUSES == (STATUS_INCOMING, STATUS_OBSERVING, STATUS_ACTIVE)


def test_k_default_30() -> None:
    assert K_DEFAULT == 30


def test_init_creates_165_incoming_cells(tmp_db) -> None:
    """init_cells_from_layer1 matérialise exactement les cellules
    actives de la Couche 1 (165 ■) en statut INCOMING."""
    with db_session(tmp_db) as conn:
        seed_macrs_layer1(conn)
        n = init_cells_from_layer1(conn)
        assert n == 165
        counts = count_cells_by_status(conn)
        assert counts.get(STATUS_INCOMING) == 165
        assert counts.get(STATUS_OBSERVING, 0) == 0
        assert counts.get(STATUS_ACTIVE, 0) == 0


def test_init_seeds_layer1_when_missing(tmp_db) -> None:
    """Si Couche 1 absente, init_cells_from_layer1 la seede tout seul."""
    with db_session(tmp_db) as conn:
        n = init_cells_from_layer1(conn)
        assert n == 165
        # Couche 1 effectivement présente
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM macrs_racines"
        ).fetchone()
        assert int(row["n"]) == 46


def test_init_is_idempotent(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        first = init_cells_from_layer1(conn)
        second = init_cells_from_layer1(conn)
        assert first == 165
        assert second == 0


def test_record_event_transitions_incoming_to_observing(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        cell_before = get_cell(conn, "R030", "Op")
        assert cell_before is not None
        assert cell_before.status == STATUS_INCOMING
        cell_after = record_event(
            conn, "R030", "Op", occurred_at="2026-07-01T08:00:00",
        )
        assert cell_after.status == STATUS_OBSERVING
        assert cell_after.n_events_total == 1
        assert cell_after.first_event_at == "2026-07-01T08:00:00"


def test_record_event_increments_counter(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        for i in range(3):
            record_event(
                conn, "R030", "Op",
                occurred_at=f"2026-07-0{i+1}T08:00:00",
            )
        cell = get_cell(conn, "R030", "Op")
        assert cell.n_events_total == 3
        assert cell.first_event_at == "2026-07-01T08:00:00"
        assert cell.last_event_at == "2026-07-03T08:00:00"


def test_record_event_rejects_inactive_couple(tmp_db) -> None:
    """R006 = Retard de commande n'a que Temp et Sync. Mat est inactif."""
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        with pytest.raises(ValueError, match="inactive|inexistante"):
            record_event(
                conn, "R006", "Mat",
                occurred_at="2026-07-01T08:00:00",
            )


def test_subdomain_activation_at_k_threshold(tmp_db) -> None:
    """K=2 sur sous-domaine 'machine' (3 racines : R030, R031, R032).
    Après 2 événements sur 'machine', toutes les cellules OBSERVING
    du sous-domaine passent en ACTIVE simultanément."""
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        # Force K=2 pour sous-domaine 'machine'
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'macrs_K_machine', 2)"
        )
        # 1er événement sur R030/Op (machine) → OBSERVING, K pas atteint
        record_event(conn, "R030", "Op", occurred_at="2026-07-01T08:00")
        assert get_cell(conn, "R030", "Op").status == STATUS_OBSERVING
        # 2e événement sur R031/Op (machine) → total = 2 = K → ACTIVE
        record_event(conn, "R031", "Op", occurred_at="2026-07-01T09:00")
        # Toutes les cellules OBSERVING du sous-domaine 'machine'
        # passent en ACTIVE
        assert get_cell(conn, "R030", "Op").status == STATUS_ACTIVE
        assert get_cell(conn, "R031", "Op").status == STATUS_ACTIVE
        # R032/Cap (machine, jamais touchée) → reste INCOMING (pas
        # OBSERVING donc pas concernée par la transition synchrone).
        # NB : R032 n'a pas d'incidence Op, on prend Cap qui existe.
        assert get_cell(conn, "R032", "Cap").status == STATUS_INCOMING


def test_activation_does_not_affect_other_subdomains(tmp_db) -> None:
    """L'activation du sous-domaine 'machine' n'affecte pas le
    sous-domaine 'methode' (Production aussi)."""
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'macrs_K_machine', 1)"
        )
        # 1 événement sur machine (R030/Op) → K=1 atteint → R030/Op ACTIVE
        record_event(conn, "R030", "Op", occurred_at="2026-07-01T08:00")
        assert get_cell(conn, "R030", "Op").status == STATUS_ACTIVE
        # R035/Op (méthode) reste INCOMING
        assert get_cell(conn, "R035", "Op").status == STATUS_INCOMING


def test_get_k_for_subdomain_default_30(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        assert get_k_for_subdomain(conn, "machine") == K_DEFAULT


def test_get_k_for_subdomain_reads_parameters(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'macrs_K_volume', 25)"
        )
        assert get_k_for_subdomain(conn, "volume") == 25


def test_list_cells_by_status(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        incoming = list_cells_by_status(conn, STATUS_INCOMING)
        assert len(incoming) == 165
        assert list_cells_by_status(conn, STATUS_OBSERVING) == []
        assert list_cells_by_status(conn, STATUS_ACTIVE) == []


def test_unique_constraint_one_cell_per_couple(tmp_db) -> None:
    import sqlite3 as _sqlite3
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        with pytest.raises(_sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO causal_cells "
                "(racine_id, categorie_code, status) "
                "VALUES ('R001', 'Mat', 'INCOMING')"
            )


def test_active_cells_remain_active_on_new_event(tmp_db) -> None:
    """Une cellule ACTIVE reste ACTIVE sur un événement supplémentaire
    (transition unidirectionnelle)."""
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'macrs_K_machine', 1)"
        )
        record_event(conn, "R030", "Op", occurred_at="2026-07-01T08:00")
        assert get_cell(conn, "R030", "Op").status == STATUS_ACTIVE
        # Nouvel événement
        cell = record_event(
            conn, "R030", "Op", occurred_at="2026-07-02T08:00",
        )
        assert cell.status == STATUS_ACTIVE
        assert cell.n_events_total == 2


def test_transitioned_observing_timestamp_recorded(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        record_event(conn, "R030", "Op", occurred_at="2026-07-01T08:00:00")
        row = conn.execute(
            "SELECT transitioned_observing_at FROM causal_cells "
            "WHERE racine_id='R030' AND categorie_code='Op'"
        ).fetchone()
        assert row["transitioned_observing_at"] == "2026-07-01T08:00:00"


def test_transitioned_active_timestamp_recorded(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'macrs_K_machine', 1)"
        )
        record_event(conn, "R030", "Op", occurred_at="2026-07-05T09:00:00")
        row = conn.execute(
            "SELECT transitioned_active_at FROM causal_cells "
            "WHERE racine_id='R030' AND categorie_code='Op'"
        ).fetchone()
        assert row["transitioned_active_at"] == "2026-07-05T09:00:00"
