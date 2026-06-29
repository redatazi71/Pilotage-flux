"""Test d'acceptation V10 phase A — random fixtures, scénarios, multi-goulots."""

from __future__ import annotations

from pathlib import Path

import pytest

from pilotage_flux.comparative import (
    DOCTRINE_EVENT,
    DOCTRINE_OF,
    DOCTRINES,
    RandomScenarioSpec,
    build_random_study_report,
    compute_kpis,
    generate_random_scenario,
    run_doctrine,
    run_random_study,
)
from pilotage_flux.data_factory import FixtureSpec, generate_random_fixtures
from pilotage_flux.gates import identify_bottlenecks


def test_l10_1_random_fixtures_generate_and_import(tmp_path: Path) -> None:
    """L10.1 : la génération produit 7 CSVs importables, et les comptes
    correspondent à la spec."""
    spec = FixtureSpec(
        n_finished_articles=5, n_semi_articles=4, n_components=6,
        n_workstations=7, n_initial_sales_orders=4,
        bottleneck_workstation_indices=[3, 5],
    )
    idx = generate_random_fixtures(spec, seed=42, out_dir=tmp_path)
    assert len(idx["finished_articles"]) == 5
    assert len(idx["semi_articles"]) == 4
    assert len(idx["components"]) == 6
    assert len(idx["workstations"]) == 7
    assert idx["bottleneck_workstations"] == ["WS-3", "WS-5"]
    # Test import
    from pilotage_flux.db import db_session, init_schema
    from pilotage_flux.importers import import_referentials

    db = tmp_path / "test.db"
    init_schema(db, drop_existing=True)
    with db_session(db) as conn:
        import_referentials(conn, tmp_path)
        n_arts = conn.execute("SELECT COUNT(*) AS n FROM articles").fetchone()["n"]
    assert n_arts == 5 + 4 + 6  # 15


def test_l10_1_random_fixtures_are_deterministic(tmp_path: Path) -> None:
    """Même seed → mêmes fichiers à l'octet près."""
    spec = FixtureSpec(n_finished_articles=4, n_workstations=5)
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    generate_random_fixtures(spec, seed=123, out_dir=dir_a)
    generate_random_fixtures(spec, seed=123, out_dir=dir_b)
    for csv_name in ("articles.csv", "workstations.csv", "bom_lines.csv",
                     "routing_operations.csv", "parameters.csv",
                     "sales_orders.csv", "calendars.csv"):
        assert (dir_a / csv_name).read_text() == (dir_b / csv_name).read_text()


def test_l10_2_random_scenario_consistent_with_fixtures(tmp_path: Path) -> None:
    """L10.2 : un scénario aléatoire référence des articles/postes/POs
    qui existent dans les fixtures."""
    spec = FixtureSpec(n_finished_articles=3, n_semi_articles=2,
                       n_components=4, n_workstations=5)
    fix_dir = tmp_path / "fix"
    idx = generate_random_fixtures(spec, seed=10, out_dir=fix_dir)
    scen = generate_random_scenario(
        RandomScenarioSpec(n_sales_orders=4, n_hazards=4),
        seed=20, fixtures_dir=fix_dir,
    )
    # SOs sur des finis
    for so in scen.initial_sales_orders:
        assert so["article_id"] in idx["finished_articles"]
    # Stocks sur composants
    for art in scen.initial_stocks:
        assert art in idx["components"]
    # Aléas cohérents
    for h in scen.hazards:
        if h.kind == "breakdown_ws":
            assert h.payload["workstation_id"] in idx["workstations"]
        elif h.kind == "quality_nc":
            assert h.payload["article_id"] in (
                idx["finished_articles"] + idx["semi_articles"]
            )
        elif h.kind == "urgent_order":
            assert h.payload["article_id"] in idx["finished_articles"]


def test_l10_3_identify_bottlenecks_returns_ranked(
    tmp_path: Path
) -> None:
    """L10.3 : identify_bottlenecks renvoie une liste ordonnée par ratio
    décroissant, et seuils respectés."""
    from pilotage_flux.aps import compute_candidates
    from pilotage_flux.db import db_session, init_schema
    from pilotage_flux.flux import create_contract, compute_coherence
    from pilotage_flux.gates import (
        evaluate_p3_collective_with_multi_bottlenecks,
        run_p2_on_libre_zone,
    )
    from pilotage_flux.importers import import_referentials
    from pilotage_flux.risk_debt import extinguish_risk_debt, list_risk_debts

    spec = FixtureSpec(
        n_finished_articles=3, n_workstations=6,
        bottleneck_workstation_indices=[2, 4],
    )
    fix_dir = tmp_path / "fix"
    generate_random_fixtures(spec, seed=50, out_dir=fix_dir)
    scen = generate_random_scenario(
        RandomScenarioSpec(n_sales_orders=6, n_hazards=0, horizon_days=10),
        seed=60, fixtures_dir=fix_dir,
    )
    db = tmp_path / "t.db"
    init_schema(db, drop_existing=True)
    with db_session(db) as conn:
        import_referentials(conn, fix_dir)
        # Ré-insère les SO du scenario
        conn.execute("DELETE FROM sales_orders")
        for so in scen.initial_sales_orders:
            conn.execute(
                "INSERT INTO sales_orders (sales_order_id, article_id, quantity, due_date) "
                "VALUES (?, ?, ?, ?)",
                (so["sales_order_id"], so["article_id"], so["quantity"], so["due_date"]),
            )
        compute_candidates(conn)
        run_p2_on_libre_zone(conn)
        from datetime import datetime, timedelta
        horizon_end = (
            datetime.fromisoformat(scen.horizon_start) + timedelta(days=10)
        ).strftime("%Y-%m-%d")
        cids = [
            r["candidate_id"] for r in conn.execute(
                "SELECT candidate_id FROM candidate_orders WHERE zone='negociable'"
            )
        ]
        if not cids:
            pytest.skip("Aucun candidate négociable pour ce test")
        c = create_contract(
            conn, horizon_label="test",
            horizon_start=scen.horizon_start,
            horizon_end=horizon_end, candidate_ids=cids,
        )
        compute_coherence(conn, c.contract_id)
        for d in list_risk_debts(conn, status="open"):
            extinguish_risk_debt(conn, d.risk_debt_id, reason="test")
        _, profiles, bottlenecks = (
            evaluate_p3_collective_with_multi_bottlenecks(
                conn, [c.contract_id], bottleneck_threshold_ratio=0.0,
            )
        )
    assert len(bottlenecks) > 0
    # Tri décroissant par ratio
    ratios = [b[3] for b in bottlenecks]
    assert ratios == sorted(ratios, reverse=True)


def test_l10_4_random_study_runs_4_doctrines(tmp_path: Path) -> None:
    """L10.4 : run_random_study tourne pour 4 doctrines et agrège les KPIs."""
    study = run_random_study(
        fixture_seeds=[1, 2],
        scenario_seeds=[100, 200],
        work_dir=tmp_path / "study",
        fixture_spec=FixtureSpec(
            n_finished_articles=3, n_semi_articles=2,
            n_components=4, n_workstations=5, n_initial_sales_orders=4,
        ),
        scenario_spec=RandomScenarioSpec(
            n_sales_orders=4, n_hazards=3, horizon_days=12,
        ),
    )
    # Toutes les doctrines présentes
    for d in DOCTRINES:
        assert d in study.aggregates
        assert study.aggregates[d].n_runs == 4   # 2 fix × 2 scen
        assert study.aggregates[d].total_cost_eur_mean > 0

    # Sanity : V3 (event) ne coûte JAMAIS plus que OF
    assert (
        study.aggregates[DOCTRINE_EVENT].total_cost_eur_mean
        <= study.aggregates[DOCTRINE_OF].total_cost_eur_mean + 1.0
    )

    # Rapport généré sans erreur
    report = build_random_study_report(study)
    assert "Étude comparative aléatoire" in report
    assert "Décomposition 2×2" in report
