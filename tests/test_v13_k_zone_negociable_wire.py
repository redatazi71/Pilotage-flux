"""V13.K — Tests intégration zone négociable au runner."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from pilotage_flux.comparative.runner import run_doctrine
from pilotage_flux.comparative.random_scenario import (
    RandomScenarioSpec, generate_random_scenario,
)
from pilotage_flux.comparative.scenario import DOCTRINE_FLUX
from pilotage_flux.data_factory import DEFAULT_SPEC, generate_random_fixtures
from pilotage_flux.db import db_session
from pilotage_flux.flux.demand_contract import create_demand_contract
from pilotage_flux.flux.zone_negociable_wire import (
    create_demand_contracts_for_promoted,
    ensure_weekly_contracts_for_horizon,
    is_zone_negociable_enabled,
    snapshot_all_active_twins,
)


def _seed_ws(conn, ws_id, capa):
    conn.execute(
        "INSERT OR IGNORE INTO workstations (workstation_id, label, "
        "sequence_idx) VALUES (?, ?, 1)",
        (ws_id, ws_id),
    )
    conn.execute(
        "INSERT INTO parameters (scope, scope_ref, name, value_num) "
        "VALUES ('workstation', ?, 'capacity_factor', ?)",
        (ws_id, capa),
    )


def _seed_calendar(conn):
    conn.execute(
        "INSERT OR IGNORE INTO calendars "
        "(calendar_id, label, daily_minutes) VALUES "
        "('CAL-DEFAULT', 't', 480)"
    )


def _seed_so_and_promoted_candidate(
    conn, so_id, article, qty, due, cid,
):
    """Seed complete : article + SO + candidate promoted."""
    conn.execute(
        "INSERT OR IGNORE INTO articles (article_id, label) VALUES (?, ?)",
        (article, article),
    )
    conn.execute(
        "INSERT INTO sales_orders "
        "(sales_order_id, article_id, quantity, due_date) VALUES "
        "(?, ?, ?, ?)",
        (so_id, article, qty, due),
    )
    conn.execute(
        "INSERT INTO candidate_orders "
        "(candidate_id, sales_order_id, article_id, quantity, status) "
        "VALUES (?, ?, ?, ?, 'promoted')",
        (cid, so_id, article, qty),
    )


def test_default_flag_off(tmp_db):
    with db_session(tmp_db) as conn:
        assert is_zone_negociable_enabled(conn) is False


def test_flag_enabled_when_param_set(tmp_db):
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, 'enable_zone_negociable', 1.0)"
        )
        assert is_zone_negociable_enabled(conn) is True


def test_create_demand_contracts_for_promoted(tmp_db):
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        _seed_so_and_promoted_candidate(
            conn, "SO-1", "ART-A", 100, "2026-07-15", "CAND-1",
        )
        _seed_so_and_promoted_candidate(
            conn, "SO-2", "ART-B", 50, "2026-07-22", "CAND-2",
        )
        created = create_demand_contracts_for_promoted(conn)
        assert len(created) == 2


def test_create_demand_contracts_idempotent(tmp_db):
    """Un candidate déjà contractualisé ne recrée pas de contrat."""
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        _seed_so_and_promoted_candidate(
            conn, "SO-1", "ART-A", 100, "2026-07-15", "CAND-1",
        )
        first = create_demand_contracts_for_promoted(conn)
        assert len(first) == 1
        # 2e appel : pas de nouveau contrat
        second = create_demand_contracts_for_promoted(conn)
        assert len(second) == 0


def test_auto_sign_by_default(tmp_db):
    """Par défaut, contrat créé = doc_status 'signed' immédiatement."""
    from pilotage_flux.flux.demand_contract import get_demand_contract
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        _seed_so_and_promoted_candidate(
            conn, "SO-1", "ART-A", 100, "2026-07-15", "CAND-1",
        )
        [cid] = create_demand_contracts_for_promoted(conn)
        contract = get_demand_contract(conn, cid)
        assert contract.flux_doc_status == "signed"


def test_auto_sign_can_be_disabled(tmp_db):
    from pilotage_flux.flux.demand_contract import get_demand_contract
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        _seed_so_and_promoted_candidate(
            conn, "SO-1", "ART-A", 100, "2026-07-15", "CAND-1",
        )
        [cid] = create_demand_contracts_for_promoted(conn, auto_sign=False)
        contract = get_demand_contract(conn, cid)
        assert contract.flux_doc_status == "draft"


def test_feasibility_from_db_used_when_available(tmp_db):
    """Si feasibility persistée dans flux_candidate_feasibility, elle
    enrichit le contrat créé."""
    from pilotage_flux.flux.demand_contract import get_demand_contract
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        _seed_so_and_promoted_candidate(
            conn, "SO-1", "ART-A", 100, "2026-07-15", "CAND-1",
        )
        conn.execute(
            """INSERT INTO flux_candidate_feasibility
                (candidate_id, bottleneck_ws, goulot_load_min,
                 goulot_slot_day, launch_day, buffer_days,
                 charge_total_min, takt_min_per_unit_target,
                 wip_predicted, rho_bottleneck_run, feasible)
               VALUES ('CAND-1', 'WS-3', 400, 5, 3, 2, 1200, 12.0, 8.5,
                        0.83, 1)"""
        )
        [cid] = create_demand_contracts_for_promoted(conn)
        contract = get_demand_contract(conn, cid)
        assert contract.bottleneck_ws == "WS-3"
        assert contract.takt_target_min == 12.0
        assert contract.wip_target == 8.5
        assert contract.rho_bottleneck == 0.83
        assert contract.feasible is True


def test_ensure_weekly_contracts_for_horizon(tmp_db):
    """2 demand_contracts sur 2 semaines différentes → 2 weekly."""
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        _seed_ws(conn, "WS-3", 0.70)
        _seed_so_and_promoted_candidate(
            conn, "SO-1", "ART-A", 100, "2026-07-14", "CAND-1",
        )
        _seed_so_and_promoted_candidate(
            conn, "SO-2", "ART-B", 50, "2026-07-21", "CAND-2",
        )
        create_demand_contracts_for_promoted(conn)
        weeklies = ensure_weekly_contracts_for_horizon(
            conn, horizon_start="2026-07-13", horizon_days=30,
        )
        assert len(weeklies) == 2


def test_ensure_weekly_idempotent(tmp_db):
    """2ᵉ appel : pas de nouveaux weeklies."""
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        _seed_ws(conn, "WS-3", 0.70)
        _seed_so_and_promoted_candidate(
            conn, "SO-1", "ART-A", 100, "2026-07-14", "CAND-1",
        )
        create_demand_contracts_for_promoted(conn)
        first = ensure_weekly_contracts_for_horizon(
            conn, horizon_start="2026-07-13", horizon_days=30,
        )
        second = ensure_weekly_contracts_for_horizon(
            conn, horizon_start="2026-07-13", horizon_days=30,
        )
        assert set(first) == set(second)


def test_snapshot_all_active_twins(tmp_db):
    """Crée twin_states pour chaque weekly actif."""
    with db_session(tmp_db) as conn:
        _seed_calendar(conn)
        _seed_ws(conn, "WS-3", 0.70)
        _seed_so_and_promoted_candidate(
            conn, "SO-1", "ART-A", 100, "2026-07-14", "CAND-1",
        )
        create_demand_contracts_for_promoted(conn)
        ensure_weekly_contracts_for_horizon(
            conn, horizon_start="2026-07-13", horizon_days=30,
        )
        tids = snapshot_all_active_twins(
            conn, day=0, horizon_start="2026-07-13",
            daily_wip=5.0,
        )
        assert len(tids) == 1


def test_runner_integration_off_by_default_no_change(tmp_db):
    """Sans flag activé, le runner ne crée aucun demand/weekly."""
    with TemporaryDirectory(prefix="v13k_") as tmp:
        work = Path(tmp)
        fix_dir = work / "fix"
        generate_random_fixtures(DEFAULT_SPEC, seed=42, out_dir=fix_dir)
        spec = RandomScenarioSpec(
            n_hazards=0, n_sales_orders=5, horizon_days=15,
        )
        scen = generate_random_scenario(spec, seed=42, fixtures_dir=fix_dir)
        db = work / "test.db"
        run_doctrine(scen, DOCTRINE_FLUX, db, fixtures_dir=fix_dir)
        with db_session(db) as conn:
            n_demand = conn.execute(
                "SELECT COUNT(*) AS n FROM demand_contracts"
            ).fetchone()["n"]
            n_weekly = conn.execute(
                "SELECT COUNT(*) AS n FROM weekly_flux_contracts"
            ).fetchone()["n"]
            assert n_demand == 0
            assert n_weekly == 0


def test_runner_integration_on_creates_artifacts(tmp_db):
    """Avec flag activé, le runner crée demand + weekly + snapshots."""
    with TemporaryDirectory(prefix="v13k_") as tmp:
        work = Path(tmp)
        fix_dir = work / "fix"
        generate_random_fixtures(DEFAULT_SPEC, seed=42, out_dir=fix_dir)
        spec = RandomScenarioSpec(
            n_hazards=0, n_sales_orders=5, horizon_days=15,
        )
        scen = generate_random_scenario(spec, seed=42, fixtures_dir=fix_dir)
        db = work / "test.db"
        run_doctrine(
            scen, DOCTRINE_FLUX, db, fixtures_dir=fix_dir,
            param_overrides={
                ("global", None, "enable_zone_negociable"): 1.0,
            },
        )
        with db_session(db) as conn:
            n_demand = conn.execute(
                "SELECT COUNT(*) AS n FROM demand_contracts"
            ).fetchone()["n"]
            n_weekly = conn.execute(
                "SELECT COUNT(*) AS n FROM weekly_flux_contracts"
            ).fetchone()["n"]
            n_snapshot = conn.execute(
                "SELECT COUNT(*) AS n FROM flux_twin_states"
            ).fetchone()["n"]
            assert n_demand > 0
            assert n_weekly > 0
            assert n_snapshot > 0
