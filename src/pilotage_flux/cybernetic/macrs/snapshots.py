"""MACRS Couche 2 — A.4 : snapshots hebdo + versioning des poids.

Référence : matrice_operationnelle_specification.md §3.5 (snapshots
historique) et §5 (versioning des règles de pondération).

API snapshots :
  - take_snapshot(conn, *, now_iso, weight_version_id=None)
        snapshot toutes les cellules ACTIVE → renvoie le nombre.
  - list_snapshots / count_snapshots / get_snapshots_for_cell

API weight_versions :
  - create_weight_version(label, description, coefficients) -> id
  - activate_weight_version(id) : archive l'ancienne active
  - get_active_weight_version() -> dict | None
  - archive_weight_version(id)
  - list_weight_versions(status?)
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from pilotage_flux.cybernetic.macrs.couche2 import (
    STATUS_ACTIVE,
    aggregate_cell,
)


# -------- Statuts weight_versions --------
WV_ACTIVE = "active"
WV_ARCHIVED = "archived"
WV_EXPERIMENTAL = "experimental"
WV_STATUSES = (WV_ACTIVE, WV_ARCHIVED, WV_EXPERIMENTAL)


# ---------------------------------------------------------------------
# Weight versions
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class WeightVersion:
    weight_version_id: int
    label: str
    description: str
    coefficients: dict[str, float]
    status: str
    created_at: str
    activated_at: str | None
    archived_at: str | None


def create_weight_version(
    conn: sqlite3.Connection,
    *,
    label: str,
    description: str,
    coefficients: dict[str, float],
    status: str = WV_EXPERIMENTAL,
) -> int:
    """Crée une nouvelle version de pondération.

    `coefficients` : mapping {nom_indicateur: coefficient_float}.
    Par défaut status='experimental' (non utilisé par le Pareto).
    """
    if status not in WV_STATUSES:
        raise ValueError(
            f"status invalide : {status} (attendu {WV_STATUSES})"
        )
    cur = conn.execute(
        "INSERT INTO weight_versions "
        "(label, description, coefficients_json, status) "
        "VALUES (?, ?, ?, ?)",
        (label, description, json.dumps(coefficients), status),
    )
    return int(cur.lastrowid)


def activate_weight_version(
    conn: sqlite3.Connection, weight_version_id: int,
) -> None:
    """Marque une version comme `active`.

    Archive automatiquement la version précédemment active (s'il y
    en a une) — invariant spec §5.2 : une seule version active à un
    instant donné.
    """
    target = conn.execute(
        "SELECT weight_version_id, status FROM weight_versions "
        "WHERE weight_version_id = ?",
        (weight_version_id,),
    ).fetchone()
    if target is None:
        raise ValueError(f"weight_version_id {weight_version_id} introuvable")
    # Archive l'ancienne active (s'il y en a une autre)
    conn.execute(
        "UPDATE weight_versions "
        "SET status = 'archived', archived_at = datetime('now') "
        "WHERE status = 'active' AND weight_version_id != ?",
        (weight_version_id,),
    )
    # Active la cible
    conn.execute(
        "UPDATE weight_versions "
        "SET status = 'active', activated_at = datetime('now'), "
        "    archived_at = NULL "
        "WHERE weight_version_id = ?",
        (weight_version_id,),
    )


def get_active_weight_version(
    conn: sqlite3.Connection,
) -> WeightVersion | None:
    row = conn.execute(
        "SELECT * FROM weight_versions WHERE status = 'active' LIMIT 1"
    ).fetchone()
    return _row_to_version(row) if row else None


def archive_weight_version(
    conn: sqlite3.Connection, weight_version_id: int,
) -> None:
    conn.execute(
        "UPDATE weight_versions "
        "SET status = 'archived', archived_at = datetime('now') "
        "WHERE weight_version_id = ?",
        (weight_version_id,),
    )


def list_weight_versions(
    conn: sqlite3.Connection, *, status: str | None = None,
) -> list[WeightVersion]:
    sql = "SELECT * FROM weight_versions"
    params: list[object] = []
    if status is not None:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY weight_version_id"
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_version(r) for r in rows]


def _row_to_version(row: sqlite3.Row) -> WeightVersion:
    return WeightVersion(
        weight_version_id=int(row["weight_version_id"]),
        label=row["label"],
        description=row["description"],
        coefficients=json.loads(row["coefficients_json"]),
        status=row["status"],
        created_at=row["created_at"],
        activated_at=row["activated_at"],
        archived_at=row["archived_at"],
    )


# ---------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class CellSnapshot:
    snapshot_id: int
    cell_id: int
    racine_id: str
    categorie_code: str
    status: str
    snapshot_at: str
    n_w_courte: int
    n_w_longue: int
    n_cumul: int
    ratio_emergence: float | None
    histogram_w_courte: dict[str, int]
    histogram_w_longue: dict[str, int]
    histogram_cumul: dict[str, int]
    weight_version_id: int | None


def take_snapshot(
    conn: sqlite3.Connection,
    *,
    now_iso: str,
    weight_version_id: int | None = None,
) -> int:
    """Snapshot toutes les cellules ACTIVE à l'instant `now_iso`.

    Immuable une fois écrit. À appeler à la fréquence hebdomadaire
    simulée (7 jours) cf. spec §3.5.

    Si `weight_version_id` est None, utilise la version active
    courante (s'il y en a une) — référence audit.

    Renvoie le nombre de cellules snapshotées.
    """
    # Résout weight_version_id si non fourni
    if weight_version_id is None:
        active = get_active_weight_version(conn)
        weight_version_id = (
            active.weight_version_id if active is not None else None
        )

    active_cells = conn.execute(
        "SELECT cell_id FROM causal_cells WHERE status = ?",
        (STATUS_ACTIVE,),
    ).fetchall()

    n_taken = 0
    for r in active_cells:
        agg = aggregate_cell(conn, int(r["cell_id"]), now_iso=now_iso)
        conn.execute(
            """
            INSERT INTO causal_cell_snapshots
                (cell_id, racine_id, categorie_code, status, snapshot_at,
                 n_w_courte, n_w_longue, n_cumul, ratio_emergence,
                 histogram_w_courte_json, histogram_w_longue_json,
                 histogram_cumul_json, weight_version_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                agg.cell_id, agg.racine_id, agg.categorie_code,
                agg.status, now_iso,
                agg.n_w_courte, agg.n_w_longue, agg.n_cumul,
                agg.ratio_emergence,
                json.dumps(agg.histogram_w_courte),
                json.dumps(agg.histogram_w_longue),
                json.dumps(agg.histogram_cumul),
                weight_version_id,
            ),
        )
        n_taken += 1
    return n_taken


def count_snapshots(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM causal_cell_snapshots"
    ).fetchone()
    return int(row["n"]) if row else 0


def get_snapshots_for_cell(
    conn: sqlite3.Connection,
    cell_id: int,
    *,
    from_iso: str | None = None,
    to_iso: str | None = None,
) -> list[CellSnapshot]:
    sql = ("SELECT * FROM causal_cell_snapshots "
           "WHERE cell_id = ?")
    params: list[object] = [cell_id]
    if from_iso is not None:
        sql += " AND snapshot_at >= ?"
        params.append(from_iso)
    if to_iso is not None:
        sql += " AND snapshot_at <= ?"
        params.append(to_iso)
    sql += " ORDER BY snapshot_at"
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_snapshot(r) for r in rows]


def list_snapshots_at(
    conn: sqlite3.Connection, snapshot_at: str,
) -> list[CellSnapshot]:
    rows = conn.execute(
        "SELECT * FROM causal_cell_snapshots WHERE snapshot_at = ? "
        "ORDER BY cell_id",
        (snapshot_at,),
    ).fetchall()
    return [_row_to_snapshot(r) for r in rows]


def _row_to_snapshot(row: sqlite3.Row) -> CellSnapshot:
    return CellSnapshot(
        snapshot_id=int(row["snapshot_id"]),
        cell_id=int(row["cell_id"]),
        racine_id=row["racine_id"],
        categorie_code=row["categorie_code"],
        status=row["status"],
        snapshot_at=row["snapshot_at"],
        n_w_courte=int(row["n_w_courte"]),
        n_w_longue=int(row["n_w_longue"]),
        n_cumul=int(row["n_cumul"]),
        ratio_emergence=(
            float(row["ratio_emergence"])
            if row["ratio_emergence"] is not None else None
        ),
        histogram_w_courte=json.loads(row["histogram_w_courte_json"]),
        histogram_w_longue=json.loads(row["histogram_w_longue_json"]),
        histogram_cumul=json.loads(row["histogram_cumul_json"]),
        weight_version_id=(
            int(row["weight_version_id"])
            if row["weight_version_id"] is not None else None
        ),
    )
