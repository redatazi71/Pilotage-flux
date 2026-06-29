"""Wrapper de connexion SQLite et initialisation du schema."""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path
from typing import Iterator
from contextlib import contextmanager


SCHEMA_RESOURCE = ("pilotage_flux.db", "schema.sql")


def get_schema_sql() -> str:
    """Charge le schema.sql depuis les ressources du package."""
    pkg, name = SCHEMA_RESOURCE
    return resources.files(pkg).joinpath(name).read_text(encoding="utf-8")


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Ouvre une connexion SQLite avec foreign keys et row_factory dict-like."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(db_path: Path | str, *, drop_existing: bool = False) -> Path:
    """Cree (ou recree) la base avec le schema V0.

    Renvoie le chemin de la base.
    """
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if drop_existing and p.exists():
        p.unlink()
    conn = connect(p)
    try:
        conn.executescript(get_schema_sql())
        conn.execute(
            "INSERT OR REPLACE INTO run_metadata (key, value) VALUES (?, ?)",
            ("schema_version", "v0.1"),
        )
    finally:
        conn.close()
    return p


@contextmanager
def db_session(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    """Context manager : ouvre, yield, ferme."""
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()
