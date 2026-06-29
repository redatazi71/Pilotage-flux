"""Test de l'import CSV des referentiels."""

from pathlib import Path

from pilotage_flux.db import db_session
from pilotage_flux.importers import import_referentials


def test_import_all_fixtures(tmp_db: Path, fixtures_dir: Path) -> None:
    with db_session(tmp_db) as conn:
        results = import_referentials(conn, fixtures_dir)

    by_table = {r.table: r for r in results}
    assert by_table["articles"].rows_inserted == 3
    assert by_table["workstations"].rows_inserted == 3
    assert by_table["calendars"].rows_inserted == 1
    assert by_table["routing_operations"].rows_inserted == 3
    assert by_table["bom_lines"].rows_inserted == 2
    assert by_table["parameters"].rows_inserted == 8
    assert by_table["sales_orders"].rows_inserted == 2


def test_import_is_atomic_on_failure(tmp_db: Path, tmp_path: Path) -> None:
    """Si une fixture obligatoire manque, aucune insertion ne reste."""
    broken_dir = tmp_path / "broken"
    broken_dir.mkdir()
    # Articles only - workstations.csv manquant (obligatoire)
    (broken_dir / "articles.csv").write_text(
        "article_id,label,unit,is_purchased\nA,Label,PCE,0\n", encoding="utf-8"
    )
    try:
        with db_session(tmp_db) as conn:
            import_referentials(conn, broken_dir)
        raise AssertionError("Should have raised FileNotFoundError")
    except FileNotFoundError:
        pass

    with db_session(tmp_db) as conn:
        (n,) = conn.execute("SELECT COUNT(*) FROM articles").fetchone()
    assert n == 0, "Le rollback aurait du annuler les inserts articles"


def test_parameters_data_driven_proof(tmp_db: Path, fixtures_dir: Path) -> None:
    """Demontre que les capacites sont en base et modifiables."""
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_dir)
        before = conn.execute(
            "SELECT value_num FROM parameters "
            "WHERE scope='workstation' AND scope_ref='WS-1' AND name='capacity_factor'"
        ).fetchone()
        assert before["value_num"] == 0.85

        # Simule un changement de capacite sans toucher au code
        conn.execute(
            "UPDATE parameters SET value_num = 0.95 "
            "WHERE scope='workstation' AND scope_ref='WS-1' AND name='capacity_factor'"
        )
        after = conn.execute(
            "SELECT value_num FROM parameters "
            "WHERE scope='workstation' AND scope_ref='WS-1' AND name='capacity_factor'"
        ).fetchone()
    assert after["value_num"] == 0.95
