"""Import CSV des referentiels V0.

Lit les fichiers attendus dans un dossier `fixtures/` :
    - articles.csv
    - workstations.csv
    - calendars.csv
    - bom_lines.csv
    - routing_operations.csv
    - parameters.csv
    - sales_orders.csv

Chaque fichier suit le schema SQLite de la table correspondante.
"""

from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass
class ImportResult:
    table: str
    rows_inserted: int
    skipped: int = 0


# (filename, table, columns, optional)
IMPORT_PLAN: list[tuple[str, str, list[str], bool]] = [
    (
        "articles.csv",
        "articles",
        ["article_id", "label", "unit", "is_purchased"],
        False,
    ),
    (
        "workstations.csv",
        "workstations",
        ["workstation_id", "label", "sequence_idx"],
        False,
    ),
    (
        "calendars.csv",
        "calendars",
        ["calendar_id", "label", "daily_minutes", "working_days"],
        False,
    ),
    (
        "bom_lines.csv",
        "bom_lines",
        ["parent_article", "child_article", "quantity"],
        True,  # optionnel pour V0 (mono-niveau possible sans BOM)
    ),
    (
        "routing_operations.csv",
        "routing_operations",
        ["article_id", "sequence_idx", "workstation_id", "unit_time_min"],
        False,
    ),
    (
        "parameters.csv",
        "parameters",
        ["scope", "scope_ref", "name", "value_num", "value_text"],
        True,
    ),
    (
        "sales_orders.csv",
        "sales_orders",
        ["sales_order_id", "article_id", "quantity", "due_date"],
        True,
    ),
]


def _coerce(value: str) -> str | int | float | None:
    """Convertit une cellule CSV : '' -> None, sinon str (sqlite gere les casts)."""
    if value == "":
        return None
    return value


def _import_one(
    conn: sqlite3.Connection,
    fixtures_dir: Path,
    filename: str,
    table: str,
    columns: list[str],
    optional: bool,
) -> ImportResult:
    path = fixtures_dir / filename
    if not path.exists():
        if optional:
            return ImportResult(table=table, rows_inserted=0, skipped=1)
        raise FileNotFoundError(f"Fixture manquante : {path}")

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [c for c in columns if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(
                f"{filename} : colonnes manquantes {missing} "
                f"(trouve : {reader.fieldnames})"
            )
        placeholders = ", ".join(["?"] * len(columns))
        sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
        rows: Iterable[tuple] = (
            tuple(_coerce(row[c]) for c in columns) for row in reader
        )
        cur = conn.executemany(sql, rows)
        return ImportResult(table=table, rows_inserted=cur.rowcount)


def import_referentials(
    conn: sqlite3.Connection,
    fixtures_dir: Path,
) -> list[ImportResult]:
    """Importe tous les referentiels presents dans `fixtures_dir`.

    Le foreign key check exige un ordre : articles, postes, calendars
    avant BOM et routing.
    """
    results: list[ImportResult] = []
    conn.execute("BEGIN")
    try:
        for filename, table, cols, optional in IMPORT_PLAN:
            results.append(_import_one(conn, fixtures_dir, filename, table, cols, optional))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return results
