"""Zone négociable → grain opération : PCs matérialisés après P3 freeze."""

from __future__ import annotations

from pathlib import Path

import pytest

from pilotage_flux.comparative.bce_wire import (
    bce_distribute_pcs_after_freeze,
    bce_kpis,
)
from pilotage_flux.comparative.runner import run_doctrine
from pilotage_flux.comparative.scenario import (
    DOCTRINE_EVENT,
    DOCTRINE_EVENT_BCE,
    DOCTRINE_OF_EVENT,
    DOCTRINE_OF_EVENT_BCE,
    baseline_scenario,
)
from pilotage_flux.db import db_session


FIXTURES = Path("data/fixtures_v1")


# ---------------------------------------------------------------------
# bce_distribute_pcs_after_freeze — sémantique
# ---------------------------------------------------------------------

def test_distribute_skips_non_bce(tmp_db) -> None:
    """Doctrine non-BCE → no-op (renvoie None)."""
    with db_session(tmp_db) as conn:
        result = bce_distribute_pcs_after_freeze(
            conn, "FZ-X", DOCTRINE_EVENT,
        )
        assert result is None


def test_distribute_handles_empty_batch(tmp_db) -> None:
    """Batch inexistant + doctrine BCE → renvoie dict avec n_pcs=0."""
    with db_session(tmp_db) as conn:
        result = bce_distribute_pcs_after_freeze(
            conn, "FZ-INEXISTANT", DOCTRINE_EVENT_BCE,
        )
        assert result is not None
        assert result["n_pcs"] == 0
        # Soit pcs_via='direct_ofs' (pas d'OFs non plus) soit erreur
        # tracée
        assert result["pcs_via"] in ("direct_ofs", "flux_contract")


# ---------------------------------------------------------------------
# Smoke runner — vérifie que les PCs sont effectivement matérialisés
# ---------------------------------------------------------------------

@pytest.mark.skipif(not FIXTURES.exists(),
                    reason="fixtures_v1 absentes")
def test_event_bce_materializes_pcs_via_flux_contracts(tmp_path: Path) -> None:
    """FLUX+EVENT+BCE : la zone négociable produit un contrat,
    P3 le gèle, distribute_contracts_at_p3_exit matérialise les PCs."""
    scenario = baseline_scenario()
    db = tmp_path / "evbce.db"
    result = run_doctrine(
        scenario, DOCTRINE_EVENT_BCE, db,
        fixtures_dir=FIXTURES,
        evaluate_rejections=False,
    )
    with db_session(db) as conn:
        kpis = bce_kpis(conn)
        # PCs effectivement créés (au moins 1 par OF)
        assert kpis["pcs_total"] >= 1
        # Tous les PCs créés ont origin_kind='flux_contract'
        rows = conn.execute(
            "SELECT origin_kind FROM production_contracts"
        ).fetchall()
        kinds = {r["origin_kind"] for r in rows}
        assert kinds == {"flux_contract"}
    # Le note runner mentionne le wiring
    notes = " ".join(result.notes)
    assert "BCE PCs" in notes
    assert "flux_contract" in notes


@pytest.mark.skipif(not FIXTURES.exists(),
                    reason="fixtures_v1 absentes")
def test_of_event_bce_materializes_pcs_via_direct_ofs(tmp_path: Path) -> None:
    """OF+EVENT+BCE : pas de contrat flux → fallback per-OF, PCs avec
    origin_kind='candidate' ou 'sales_order'."""
    scenario = baseline_scenario()
    db = tmp_path / "ofevbce.db"
    result = run_doctrine(
        scenario, DOCTRINE_OF_EVENT_BCE, db,
        fixtures_dir=FIXTURES,
        evaluate_rejections=False,
    )
    with db_session(db) as conn:
        kpis = bce_kpis(conn)
        assert kpis["pcs_total"] >= 1
        rows = conn.execute(
            "SELECT origin_kind FROM production_contracts"
        ).fetchall()
        kinds = {r["origin_kind"] for r in rows}
        # Devrait être 'candidate' ou 'sales_order', pas
        # 'flux_contract'
        assert "flux_contract" not in kinds
        assert kinds <= {"candidate", "sales_order"}
    notes = " ".join(result.notes)
    assert "BCE PCs" in notes
    assert "direct_ofs" in notes


@pytest.mark.skipif(not FIXTURES.exists(),
                    reason="fixtures_v1 absentes")
def test_event_non_bce_does_not_create_pcs(tmp_path: Path) -> None:
    """Doctrine FLUX+EVENT (non-BCE) : aucun PC créé."""
    scenario = baseline_scenario()
    db = tmp_path / "ev_non_bce.db"
    run_doctrine(
        scenario, DOCTRINE_EVENT, db,
        fixtures_dir=FIXTURES,
        evaluate_rejections=False,
    )
    with db_session(db) as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM production_contracts"
        ).fetchone()["n"]
        assert n == 0


@pytest.mark.skipif(not FIXTURES.exists(),
                    reason="fixtures_v1 absentes")
def test_of_event_non_bce_does_not_create_pcs(tmp_path: Path) -> None:
    scenario = baseline_scenario()
    db = tmp_path / "of_ev_non_bce.db"
    run_doctrine(
        scenario, DOCTRINE_OF_EVENT, db,
        fixtures_dir=FIXTURES,
        evaluate_rejections=False,
    )
    with db_session(db) as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM production_contracts"
        ).fetchone()["n"]
        assert n == 0


@pytest.mark.skipif(not FIXTURES.exists(),
                    reason="fixtures_v1 absentes")
def test_event_bce_pcs_target_T_qty_cost_per_op(tmp_path: Path) -> None:
    """Vérifie que chaque PC porte bien T, Er, C cibles non triviaux."""
    scenario = baseline_scenario()
    db = tmp_path / "ev_pc_struct.db"
    run_doctrine(
        scenario, DOCTRINE_EVENT_BCE, db,
        fixtures_dir=FIXTURES,
        evaluate_rejections=False,
    )
    with db_session(db) as conn:
        rows = conn.execute(
            "SELECT target_time_min, target_qty_good, target_cost "
            "FROM production_contracts LIMIT 5"
        ).fetchall()
        assert len(rows) >= 1
        for r in rows:
            assert r["target_time_min"] > 0
            assert r["target_qty_good"] > 0
            # target_cost peut être 0 si hourly_rate non paramétré
            # mais doit être >= 0
            assert r["target_cost"] >= 0


@pytest.mark.skipif(not FIXTURES.exists(),
                    reason="fixtures_v1 absentes")
def test_event_bce_pcs_kpis_in_bce_kpis(tmp_path: Path) -> None:
    """Le dict bce_kpis() expose pcs_total et pcs_by_status."""
    scenario = baseline_scenario()
    db = tmp_path / "ev_kpis.db"
    run_doctrine(
        scenario, DOCTRINE_EVENT_BCE, db,
        fixtures_dir=FIXTURES,
        evaluate_rejections=False,
    )
    with db_session(db) as conn:
        kpis = bce_kpis(conn)
        assert "pcs_total" in kpis
        assert "pcs_by_status" in kpis
        # Au début tous les PCs sont 'open'
        assert kpis["pcs_by_status"].get("open", 0) == kpis["pcs_total"]
