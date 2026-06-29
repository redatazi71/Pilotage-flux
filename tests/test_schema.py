"""Test du schema SQLite V0."""

from pathlib import Path

from pilotage_flux.db import db_session, init_schema


EXPECTED_TABLES = {
    "articles",
    "workstations",
    "calendars",
    "bom_lines",
    "routing_operations",
    "parameters",
    "sales_orders",
    "candidate_orders",
    "manufacturing_orders",
    "order_operations",
    "mes_declarations",
    "event_store",
    "gate_decisions",
    "run_metadata",
}


def test_init_schema_creates_all_tables(tmp_path: Path) -> None:
    db = init_schema(tmp_path / "schema.db", drop_existing=True)
    with db_session(db) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    names = {r["name"] for r in rows}
    missing = EXPECTED_TABLES - names
    assert not missing, f"Tables manquantes : {missing}"


def test_foreign_keys_enabled(tmp_db: Path) -> None:
    with db_session(tmp_db) as conn:
        (val,) = conn.execute("PRAGMA foreign_keys").fetchone()
    assert val == 1


def test_run_metadata_seeded(tmp_db: Path) -> None:
    with db_session(tmp_db) as conn:
        row = conn.execute(
            "SELECT value FROM run_metadata WHERE key='schema_version'"
        ).fetchone()
    assert row is not None
    assert row["value"] == "v0.1"


def test_drop_existing_replaces_db(tmp_path: Path) -> None:
    db = tmp_path / "drop.db"
    init_schema(db, drop_existing=True)
    with db_session(db) as conn:
        conn.execute(
            "INSERT INTO articles (article_id, label) VALUES ('X', 'Test')"
        )
    init_schema(db, drop_existing=True)
    with db_session(db) as conn:
        (n,) = conn.execute("SELECT COUNT(*) FROM articles").fetchone()
    assert n == 0
