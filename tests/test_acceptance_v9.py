"""Test d'acceptation V9 — fixtures étendues, multi-contrats, smoothing, P3 collective."""

from __future__ import annotations

from pathlib import Path

import pytest

from pilotage_flux.comparative import (
    ALL_SCENARIOS_XL,
    DOCTRINE_EVENT,
    DOCTRINE_FLUX,
    DOCTRINE_OF,
    DOCTRINES,
    baseline_xl_scenario,
    compute_kpis,
    run_doctrine,
    stress_multi_contract_overload_scenario,
)
from pilotage_flux.comparative.scenario import DOCTRINE_OF_EVENT


@pytest.fixture
def fixtures_xl_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "fixtures_extended"


def test_l91_extended_fixtures_load(
    tmp_path: Path, fixtures_xl_dir: Path
) -> None:
    """Les fixtures étendues importent : 13 articles, 6 postes, BOM 3 niveaux."""
    from pilotage_flux.aps import compute_candidates
    from pilotage_flux.db import db_session, init_schema
    from pilotage_flux.importers import import_referentials

    db = tmp_path / "ext.db"
    init_schema(db, drop_existing=True)
    with db_session(db) as conn:
        import_referentials(conn, fixtures_xl_dir)
        n_arts = conn.execute("SELECT COUNT(*) AS n FROM articles").fetchone()["n"]
        n_ws = conn.execute("SELECT COUNT(*) AS n FROM workstations").fetchone()["n"]
        n_bom = conn.execute("SELECT COUNT(*) AS n FROM bom_lines").fetchone()["n"]
        n_rt = conn.execute(
            "SELECT COUNT(*) AS n FROM routing_operations"
        ).fetchone()["n"]
    assert n_arts == 13   # 4 finis + 4 semi + 5 composants
    assert n_ws == 6
    assert n_bom == 19    # cf. bom_lines.csv
    assert n_rt == 22     # cf. routing_operations.csv


def test_l92_multi_contracts_triggered_on_multi_articles(
    tmp_path: Path, fixtures_xl_dir: Path
) -> None:
    """baseline_xl avec 4 articles finis → P3 collective avec 4 contrats gelés."""
    scen = baseline_xl_scenario()
    result = run_doctrine(
        scen, DOCTRINE_FLUX, tmp_path / "flux.db",
        fixtures_dir=fixtures_xl_dir,
    )
    notes = " ".join(result.notes)
    assert "P3 collective" in notes
    assert "frozen=4" in notes
    assert "bottleneck=WS-3" in notes


def test_l92_partial_freeze_on_overload(
    tmp_path: Path, fixtures_xl_dir: Path
) -> None:
    """stress_multi_contract_overload exerce PARTIAL_FREEZE (capacité saturée)."""
    scen = stress_multi_contract_overload_scenario()
    result = run_doctrine(
        scen, DOCTRINE_EVENT, tmp_path / "event.db",
        fixtures_dir=fixtures_xl_dir,
    )
    notes = " ".join(result.notes)
    assert "PARTIAL_FREEZE" in notes
    assert "deferred=" in notes


def test_l94_smoothing_reduces_cost_on_xl_baseline(
    tmp_path: Path, fixtures_xl_dir: Path
) -> None:
    """L9.4 : avec smoothing actif, FLUX coûte significativement moins que OF
    sur baseline_xl (le lissage évite la congestion goulot WS-3)."""
    scen = baseline_xl_scenario()
    of_k = compute_kpis(
        scen,
        run_doctrine(scen, DOCTRINE_OF, tmp_path / "of.db",
                     fixtures_dir=fixtures_xl_dir),
    )
    flux_k = compute_kpis(
        scen,
        run_doctrine(scen, DOCTRINE_FLUX, tmp_path / "flux.db",
                     fixtures_dir=fixtures_xl_dir),
    )
    savings = of_k.total_cost_eur - flux_k.total_cost_eur
    assert savings > 5000, (
        f"Le smoothing FLUX doit sauver >5000€ vs OF sur baseline_xl, "
        f"observé : {savings:.0f}"
    )
    # Aussi : FLUX a un lead time inférieur (moins de congestion)
    assert flux_k.lead_time_days_avg < of_k.lead_time_days_avg


def test_l9_event_dominates_or_equals_of_event_on_xl(
    tmp_path: Path, fixtures_xl_dir: Path
) -> None:
    """Sur baseline_xl, EVENT (flux+event) ≤ OF+EVENT (event seul) sur coût.

    C'est la signature de l'apport propre du flux : avec multi-articles +
    smoothing, EVENT doit faire au moins aussi bien que OF+EVENT.
    """
    scen = baseline_xl_scenario()
    of_event = compute_kpis(
        scen,
        run_doctrine(scen, DOCTRINE_OF_EVENT, tmp_path / "of_event.db",
                     fixtures_dir=fixtures_xl_dir),
    )
    event = compute_kpis(
        scen,
        run_doctrine(scen, DOCTRINE_EVENT, tmp_path / "event.db",
                     fixtures_dir=fixtures_xl_dir),
    )
    assert event.total_cost_eur <= of_event.total_cost_eur + 1.0
    assert event.lead_time_days_avg <= of_event.lead_time_days_avg + 0.01


def test_l9_additivity_on_double_breakdown_xl(
    tmp_path: Path, fixtures_xl_dir: Path
) -> None:
    """Sur stress_double_breakdown_xl, l'apport flux et event sont additifs :
    Δ EVENT > Δ FLUX seul ET Δ EVENT > Δ OF+EVENT seul.
    """
    from pilotage_flux.comparative import stress_double_breakdown_xl_scenario

    scen = stress_double_breakdown_xl_scenario()
    of_k = compute_kpis(
        scen,
        run_doctrine(scen, DOCTRINE_OF, tmp_path / "of.db",
                     fixtures_dir=fixtures_xl_dir),
    )
    flux_k = compute_kpis(
        scen,
        run_doctrine(scen, DOCTRINE_FLUX, tmp_path / "flux.db",
                     fixtures_dir=fixtures_xl_dir),
    )
    ofe_k = compute_kpis(
        scen,
        run_doctrine(scen, DOCTRINE_OF_EVENT, tmp_path / "ofe.db",
                     fixtures_dir=fixtures_xl_dir),
    )
    ev_k = compute_kpis(
        scen,
        run_doctrine(scen, DOCTRINE_EVENT, tmp_path / "ev.db",
                     fixtures_dir=fixtures_xl_dir),
    )
    flux_savings = of_k.total_cost_eur - flux_k.total_cost_eur
    ofe_savings = of_k.total_cost_eur - ofe_k.total_cost_eur
    ev_savings = of_k.total_cost_eur - ev_k.total_cost_eur
    # Additivité : combiné > chacun seul
    assert ev_savings > flux_savings, (
        f"EVENT doit dominer FLUX seul : EVENT={ev_savings:.0f}, FLUX={flux_savings:.0f}"
    )
    assert ev_savings > ofe_savings, (
        f"EVENT doit dominer OF+EVENT seul : EVENT={ev_savings:.0f}, OF+E={ofe_savings:.0f}"
    )
