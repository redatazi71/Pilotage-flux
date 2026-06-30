"""MACRS A.4 — Tests snapshots hebdo immuables + weight_versions."""

from __future__ import annotations

import pytest

from pilotage_flux.cybernetic.macrs.couche2 import (
    init_cells_from_layer1,
    record_event,
)
from pilotage_flux.cybernetic.macrs.snapshots import (
    WV_ACTIVE,
    WV_ARCHIVED,
    WV_EXPERIMENTAL,
    WV_STATUSES,
    activate_weight_version,
    archive_weight_version,
    count_snapshots,
    create_weight_version,
    get_active_weight_version,
    get_snapshots_for_cell,
    list_snapshots_at,
    list_weight_versions,
    take_snapshot,
)
from pilotage_flux.db import db_session


# ---------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------

def test_wv_statuses_canonical() -> None:
    assert WV_STATUSES == (WV_ACTIVE, WV_ARCHIVED, WV_EXPERIMENTAL)


# ---------------------------------------------------------------------
# weight_versions : création, activation, archivage
# ---------------------------------------------------------------------

def test_create_weight_version_default_experimental(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        wv_id = create_weight_version(
            conn,
            label="v0.1",
            description="initiale",
            coefficients={"impact": 1.0, "criticite": 0.5},
        )
        v = list_weight_versions(conn)[0]
        assert v.weight_version_id == wv_id
        assert v.label == "v0.1"
        assert v.status == WV_EXPERIMENTAL
        assert v.coefficients == {"impact": 1.0, "criticite": 0.5}


def test_create_weight_version_invalid_status_raises(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        with pytest.raises(ValueError, match="status invalide"):
            create_weight_version(
                conn, label="x", description="x",
                coefficients={}, status="rouge",
            )


def test_activate_weight_version_archives_previous(tmp_db) -> None:
    """Invariant : une seule version active à la fois."""
    with db_session(tmp_db) as conn:
        id1 = create_weight_version(
            conn, label="v1", description="première",
            coefficients={"a": 1.0},
        )
        id2 = create_weight_version(
            conn, label="v2", description="deuxième",
            coefficients={"a": 2.0},
        )
        activate_weight_version(conn, id1)
        activate_weight_version(conn, id2)
        # v1 → archivée, v2 → active
        v1 = list_weight_versions(conn)[0]
        v2 = list_weight_versions(conn)[1]
        assert v1.status == WV_ARCHIVED
        assert v1.archived_at is not None
        assert v2.status == WV_ACTIVE
        assert v2.activated_at is not None


def test_get_active_weight_version_returns_none_when_no_active(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        create_weight_version(
            conn, label="exp", description="x", coefficients={},
        )
        assert get_active_weight_version(conn) is None


def test_get_active_weight_version_returns_active(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        wv_id = create_weight_version(
            conn, label="v1", description="x", coefficients={"k": 1.5},
        )
        activate_weight_version(conn, wv_id)
        active = get_active_weight_version(conn)
        assert active is not None
        assert active.label == "v1"
        assert active.status == WV_ACTIVE
        assert active.coefficients == {"k": 1.5}


def test_activate_unknown_id_raises(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        with pytest.raises(ValueError, match="introuvable"):
            activate_weight_version(conn, 9999)


def test_archive_weight_version(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        wv_id = create_weight_version(
            conn, label="v1", description="x", coefficients={},
        )
        activate_weight_version(conn, wv_id)
        archive_weight_version(conn, wv_id)
        v = list_weight_versions(conn)[0]
        assert v.status == WV_ARCHIVED
        assert v.archived_at is not None


def test_list_weight_versions_filter_by_status(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        id1 = create_weight_version(
            conn, label="v1", description="x", coefficients={},
        )
        create_weight_version(
            conn, label="v2", description="x", coefficients={},
        )
        activate_weight_version(conn, id1)
        active = list_weight_versions(conn, status=WV_ACTIVE)
        assert len(active) == 1
        assert active[0].label == "v1"
        exp = list_weight_versions(conn, status=WV_EXPERIMENTAL)
        assert len(exp) == 1
        assert exp[0].label == "v2"


def test_label_unique_constraint(tmp_db) -> None:
    import sqlite3 as _sqlite3
    with db_session(tmp_db) as conn:
        create_weight_version(
            conn, label="v1", description="x", coefficients={},
        )
        with pytest.raises(_sqlite3.IntegrityError):
            create_weight_version(
                conn, label="v1", description="y", coefficients={},
            )


# ---------------------------------------------------------------------
# snapshots : take_snapshot ne snapshote que les cellules ACTIVE
# ---------------------------------------------------------------------

def test_take_snapshot_empty_when_no_active(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        n = take_snapshot(conn, now_iso="2026-07-15T00:00:00")
        assert n == 0
        assert count_snapshots(conn) == 0


def test_take_snapshot_captures_active_cells_only(tmp_db) -> None:
    """K=1 sur sous-domaine 'machine' (R030, R031, R032).
    Après 1 événement sur R030/Op, toutes les cellules OBSERVING du
    sous-domaine 'machine' passent ACTIVE. R032 a 3 incidences
    (Cap, Qual, Temp) mais reste INCOMING (jamais touchée)."""
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'macrs_K_machine', 1)"
        )
        record_event(
            conn, "R030", "Op",
            occurred_at="2026-07-05T08:00:00", delay_hours=2.0,
        )
        n = take_snapshot(conn, now_iso="2026-07-15T00:00:00")
        # Seule R030/Op est ACTIVE → 1 snapshot
        assert n == 1


def test_take_snapshot_preserves_aggregates(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'macrs_K_machine', 1)"
        )
        for ts, h in (
            ("2026-07-05T08:00:00", 0.5),   # b0_1h
            ("2026-07-10T08:00:00", 2.0),   # b1_4h
            ("2026-07-12T08:00:00", 50.0),  # b1_3j
        ):
            record_event(conn, "R030", "Op",
                         occurred_at=ts, delay_hours=h)
        take_snapshot(conn, now_iso="2026-07-15T00:00:00")
        snaps = list_snapshots_at(conn, "2026-07-15T00:00:00")
        assert len(snaps) == 1
        s = snaps[0]
        assert s.racine_id == "R030"
        assert s.categorie_code == "Op"
        assert s.n_w_courte == 3
        assert s.n_w_longue == 3
        assert s.n_cumul == 3
        # Histogrammes
        assert s.histogram_w_courte["b0_1h"] == 1
        assert s.histogram_w_courte["b1_4h"] == 1
        assert s.histogram_w_courte["b1_3j"] == 1
        # Cumul = même valeurs
        assert s.histogram_cumul["b0_1h"] == 1


def test_take_snapshot_references_active_weight_version(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'macrs_K_machine', 1)"
        )
        wv_id = create_weight_version(
            conn, label="v1", description="x",
            coefficients={"impact": 1.0},
        )
        activate_weight_version(conn, wv_id)
        record_event(conn, "R030", "Op",
                     occurred_at="2026-07-05T08:00:00", delay_hours=1.0)
        take_snapshot(conn, now_iso="2026-07-15T00:00:00")
        snaps = list_snapshots_at(conn, "2026-07-15T00:00:00")
        assert snaps[0].weight_version_id == wv_id


def test_take_snapshot_explicit_weight_version(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'macrs_K_machine', 1)"
        )
        wv_id = create_weight_version(
            conn, label="v_explicit", description="x",
            coefficients={},
        )
        # Pas active mais fournie explicitement
        record_event(conn, "R030", "Op",
                     occurred_at="2026-07-05T08:00:00", delay_hours=1.0)
        take_snapshot(
            conn, now_iso="2026-07-15T00:00:00",
            weight_version_id=wv_id,
        )
        snaps = list_snapshots_at(conn, "2026-07-15T00:00:00")
        assert snaps[0].weight_version_id == wv_id


def test_snapshots_are_immutable_history(tmp_db) -> None:
    """Deux snapshots à des dates différentes coexistent (mémoire
    historique). Le 2e ne remplace pas le 1er."""
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'macrs_K_machine', 1)"
        )
        record_event(conn, "R030", "Op",
                     occurred_at="2026-07-01T08:00:00", delay_hours=1.0)
        take_snapshot(conn, now_iso="2026-07-08T00:00:00")
        # Nouvel événement entre les deux snapshots
        record_event(conn, "R030", "Op",
                     occurred_at="2026-07-10T08:00:00", delay_hours=2.0)
        take_snapshot(conn, now_iso="2026-07-15T00:00:00")
        snap1 = list_snapshots_at(conn, "2026-07-08T00:00:00")
        snap2 = list_snapshots_at(conn, "2026-07-15T00:00:00")
        assert snap1[0].n_cumul == 1
        assert snap2[0].n_cumul == 2
        # Le 1er snapshot reste inchangé
        assert snap1[0].snapshot_at == "2026-07-08T00:00:00"


def test_get_snapshots_for_cell_filtered_by_time(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'macrs_K_machine', 1)"
        )
        record_event(conn, "R030", "Op",
                     occurred_at="2026-07-01T08:00:00", delay_hours=1.0)
        for snap_at in ("2026-07-08T00:00:00",
                         "2026-07-15T00:00:00",
                         "2026-07-22T00:00:00"):
            take_snapshot(conn, now_iso=snap_at)
        cell_id = conn.execute(
            "SELECT cell_id FROM causal_cells "
            "WHERE racine_id='R030' AND categorie_code='Op'"
        ).fetchone()["cell_id"]
        # 3 snapshots au total
        assert len(get_snapshots_for_cell(conn, cell_id)) == 3
        # Filtre fenêtre [W2, W3]
        mid = get_snapshots_for_cell(
            conn, cell_id,
            from_iso="2026-07-15T00:00:00",
            to_iso="2026-07-22T00:00:00",
        )
        assert len(mid) == 2


def test_snapshot_ratio_emergence_stored(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'macrs_K_machine', 1)"
        )
        # 4 dans W_courte (récents), 2 anciens W_longue uniquement
        for ts in ("2026-07-01T08:00:00", "2026-07-02T08:00:00",
                    "2026-07-05T08:00:00", "2026-07-10T08:00:00"):
            record_event(conn, "R030", "Op",
                         occurred_at=ts, delay_hours=1.0)
        for ts in ("2026-05-01T08:00:00", "2026-05-15T08:00:00"):
            record_event(conn, "R030", "Op",
                         occurred_at=ts, delay_hours=1.0)
        take_snapshot(conn, now_iso="2026-07-15T00:00:00")
        snap = list_snapshots_at(conn, "2026-07-15T00:00:00")[0]
        assert snap.n_w_courte == 4
        assert snap.n_w_longue == 6
        # ratio = 4/6 ≈ 0.667
        assert snap.ratio_emergence is not None
        assert 0.66 < snap.ratio_emergence < 0.67
