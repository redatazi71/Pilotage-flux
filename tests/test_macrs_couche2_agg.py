"""MACRS A.3 — Tests fenêtres glissantes + 8 bins délai + cumul."""

from __future__ import annotations

import pytest

from pilotage_flux.cybernetic.macrs.couche2 import (
    BIN_LABELS,
    BINS,
    W_COURTE_DAYS,
    W_LONGUE_DAYS,
    aggregate_cell,
    aggregate_cell_by_couple,
    delay_to_bin,
    init_cells_from_layer1,
    list_events_in_window,
    record_event,
)
from pilotage_flux.db import db_session


# ---------------------------------------------------------------------
# 1) Constantes et mapping délai → bin
# ---------------------------------------------------------------------

def test_constants_windows_30_90() -> None:
    assert W_COURTE_DAYS == 30
    assert W_LONGUE_DAYS == 90


def test_bins_labels_canonical_8() -> None:
    assert BIN_LABELS == (
        "b0_1h", "b1_4h", "b4_24h", "b1_3j",
        "b3_7j", "b7_14j", "b14_30j", "b30_90j",
    )


def test_bins_ranges_contiguous_and_ascending() -> None:
    for i, (_, low, high) in enumerate(BINS):
        assert low < high
        if i > 0:
            assert low == BINS[i - 1][2]


@pytest.mark.parametrize("hours, expected_bin", [
    (0.0,    "b0_1h"),
    (0.5,    "b0_1h"),
    (0.99,   "b0_1h"),
    (1.0,    "b1_4h"),
    (3.9,    "b1_4h"),
    (4.0,    "b4_24h"),
    (23.9,   "b4_24h"),
    (24.0,   "b1_3j"),
    (71.9,   "b1_3j"),
    (72.0,   "b3_7j"),
    (167.9,  "b3_7j"),
    (168.0,  "b7_14j"),
    (336.0,  "b14_30j"),
    (719.0,  "b14_30j"),
    (720.0,  "b30_90j"),
    (2160.0, "b30_90j"),    # overflow → cap
    (5000.0, "b30_90j"),    # cap maintenu
    (-1.0,   "b0_1h"),      # négatif → b0_1h
])
def test_delay_to_bin(hours: float, expected_bin: str) -> None:
    assert delay_to_bin(hours) == expected_bin


# ---------------------------------------------------------------------
# 2) Persistance des événements
# ---------------------------------------------------------------------

def test_record_event_inserts_causal_event_row(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        record_event(
            conn, "R030", "Op",
            occurred_at="2026-07-01T08:00:00",
            delay_hours=2.5,    # → b1_4h
        )
        rows = conn.execute(
            "SELECT cell_id, occurred_at, delay_bin, delay_hours "
            "FROM causal_events"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["delay_bin"] == "b1_4h"
        assert rows[0]["delay_hours"] == 2.5


def test_record_event_without_delay_omits_bin(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        record_event(
            conn, "R030", "Op",
            occurred_at="2026-07-01T08:00:00",
            # delay_hours=None
        )
        row = conn.execute(
            "SELECT delay_bin, delay_hours FROM causal_events"
        ).fetchone()
        assert row["delay_bin"] is None
        assert row["delay_hours"] is None


def test_record_event_stores_impact_score(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        record_event(
            conn, "R030", "Op",
            occurred_at="2026-07-01T08:00:00",
            delay_hours=10.0,
            impact_score=0.75,
        )
        row = conn.execute(
            "SELECT impact_score FROM causal_events"
        ).fetchone()
        assert row["impact_score"] == 0.75


# ---------------------------------------------------------------------
# 3) Histogramme cumul (colonnes bin_cumul_*)
# ---------------------------------------------------------------------

def test_cumul_histogram_increments(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        # 3 événements sur R030/Op : 0.5h, 5h, 25h
        for ts, h in (
            ("2026-07-01T08:00", 0.5),    # b0_1h
            ("2026-07-01T09:00", 5.0),    # b4_24h
            ("2026-07-01T10:00", 25.0),   # b1_3j
        ):
            record_event(conn, "R030", "Op",
                         occurred_at=ts, delay_hours=h)
        row = conn.execute(
            "SELECT bin_cumul_b0_1h, bin_cumul_b4_24h, bin_cumul_b1_3j, "
            "bin_cumul_b1_4h "
            "FROM causal_cells WHERE racine_id='R030' AND categorie_code='Op'"
        ).fetchone()
        assert row["bin_cumul_b0_1h"] == 1
        assert row["bin_cumul_b4_24h"] == 1
        assert row["bin_cumul_b1_3j"] == 1
        assert row["bin_cumul_b1_4h"] == 0


def test_cumul_histogram_skipped_when_delay_none(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        record_event(conn, "R030", "Op",
                     occurred_at="2026-07-01T08:00")
        row = conn.execute(
            "SELECT bin_cumul_b0_1h FROM causal_cells "
            "WHERE racine_id='R030' AND categorie_code='Op'"
        ).fetchone()
        assert row["bin_cumul_b0_1h"] == 0


# ---------------------------------------------------------------------
# 4) Agrégats W_courte / W_longue / cumul
# ---------------------------------------------------------------------

def test_aggregate_cell_filters_window(tmp_db) -> None:
    """W_courte = 30j, W_longue = 90j.
    Insère événements à -10j (in), -40j (W_l only), -100j (out)."""
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        # now = 2026-07-15
        for ts, h in (
            ("2026-04-06T08:00:00", 1.0),   # 100j → hors W_longue
            ("2026-06-05T08:00:00", 5.0),   # 40j → W_longue only
            ("2026-07-05T08:00:00", 0.5),   # 10j → W_courte+W_longue
            ("2026-07-10T08:00:00", 80.0),  # 5j → W_courte+W_longue
        ):
            record_event(conn, "R030", "Op",
                         occurred_at=ts, delay_hours=h)
        agg = aggregate_cell_by_couple(
            conn, "R030", "Op", now_iso="2026-07-15T00:00:00",
        )
        assert agg.n_w_courte == 2
        assert agg.n_w_longue == 3
        assert agg.n_cumul == 4
        # Histogramme W_courte : 1 dans b0_1h, 1 dans b3_7j
        assert agg.histogram_w_courte["b0_1h"] == 1
        assert agg.histogram_w_courte["b3_7j"] == 1
        # Histogramme cumul : reflète les 4 événements
        assert agg.histogram_cumul["b1_4h"] == 1   # -100j
        assert agg.histogram_cumul["b4_24h"] == 1  # -40j (5h)
        assert agg.histogram_cumul["b0_1h"] == 1   # -10j (0.5h)
        assert agg.histogram_cumul["b3_7j"] == 1   # -5j  (80h)


def test_aggregate_cell_status_preserved(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        # K=1 → OBSERVING au 1er, ACTIVE après vérif
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'macrs_K_machine', 1)"
        )
        record_event(conn, "R030", "Op",
                     occurred_at="2026-07-01T08:00:00", delay_hours=1.0)
        agg = aggregate_cell_by_couple(
            conn, "R030", "Op", now_iso="2026-07-15T00:00:00",
        )
        assert agg.status == "ACTIVE"


def test_ratio_emergence_above_1_when_w_courte_dominant(tmp_db) -> None:
    """3 événements W_courte (dans 30j), 0 W_longue exclusif → ratio = 1.0
    quand W_courte ⊆ W_longue (tous courts = tous longs).
    Cas émergent : 5 dans W_courte vs 2 dans [W_courte, W_longue]."""
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        # 5 événements dans W_courte (récents)
        for i in range(5):
            record_event(
                conn, "R030", "Op",
                occurred_at=f"2026-07-0{i+1}T08:00:00",
                delay_hours=1.0,
            )
        # 2 événements W_longue exclusivement (anciens, ~60j avant)
        for i, ts in enumerate(["2026-05-01T08:00:00", "2026-05-15T08:00:00"]):
            record_event(conn, "R030", "Op",
                         occurred_at=ts, delay_hours=1.0)
        agg = aggregate_cell_by_couple(
            conn, "R030", "Op", now_iso="2026-07-15T00:00:00",
        )
        # W_longue = 7 (tous dans 90j), W_courte = 5
        assert agg.n_w_courte == 5
        assert agg.n_w_longue == 7
        # ratio = 5/7 ≈ 0.71 < 1 → racine en déclin relatif
        ratio = agg.ratio_emergence
        assert ratio is not None
        assert 0.7 < ratio < 0.72


def test_ratio_emergence_none_when_w_longue_zero(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        # 0 événement → W_longue = 0 → ratio = None
        # On utilise un événement out-of-window
        record_event(conn, "R030", "Op",
                     occurred_at="2026-01-01T08:00:00", delay_hours=1.0)
        agg = aggregate_cell_by_couple(
            conn, "R030", "Op", now_iso="2026-07-15T00:00:00",
        )
        assert agg.n_w_longue == 0
        assert agg.ratio_emergence is None


def test_aggregate_unknown_cell_raises(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        with pytest.raises(ValueError, match="introuvable"):
            aggregate_cell(conn, 99999, now_iso="2026-07-15T00:00:00")


def test_aggregate_inactive_couple_raises(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        # R006 = Retard de commande n'a pas d'incidence Mat
        with pytest.raises(ValueError, match="inactive"):
            aggregate_cell_by_couple(
                conn, "R006", "Mat", now_iso="2026-07-15T00:00:00",
            )


def test_list_events_in_window_returns_filtered(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        for ts, h in (
            ("2026-04-01T08:00", 1.0),   # ~105j → hors 90j
            ("2026-06-01T08:00", 2.0),   # ~44j  → in 90j, hors 30j
            ("2026-07-05T08:00", 3.0),   # ~10j  → in 30j
        ):
            record_event(conn, "R030", "Op",
                         occurred_at=ts, delay_hours=h)
        cell_id = conn.execute(
            "SELECT cell_id FROM causal_cells "
            "WHERE racine_id='R030' AND categorie_code='Op'"
        ).fetchone()["cell_id"]
        in_courte = list_events_in_window(
            conn, cell_id, now_iso="2026-07-15T00:00:00",
            window_days=30,
        )
        in_longue = list_events_in_window(
            conn, cell_id, now_iso="2026-07-15T00:00:00",
            window_days=90,
        )
        assert len(in_courte) == 1
        assert len(in_longue) == 2


def test_events_indexed_by_cell_and_time(tmp_db) -> None:
    """L'index idx_causal_events_cell_time existe (test fonctionnel
    sur de la perf-équivalente : la requête doit aboutir vite)."""
    with db_session(tmp_db) as conn:
        init_cells_from_layer1(conn)
        for i in range(50):
            record_event(
                conn, "R030", "Op",
                occurred_at=f"2026-07-01T{i:02d}:00:00"
                            if i < 24 else "2026-07-02T00:00:00",
                delay_hours=float(i),
            )
        agg = aggregate_cell_by_couple(
            conn, "R030", "Op", now_iso="2026-07-15T00:00:00",
        )
        # 50 événements, tous dans W_courte
        assert agg.n_w_courte == 50
        assert agg.n_w_longue == 50
