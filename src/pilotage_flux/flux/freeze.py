"""Tranche gelée (freeze_batch) - §7 bis.2 du cadrage.

Une tranche gelée est une photographie immuable d'un périmètre de contrats
de flux cohérent gelé pour exécution. Elle référence une version figée de
chaque contrat inclus. Toute modification ultérieure d'un contrat ne touche
PAS la tranche : on créerait une nouvelle tranche.

V1.5 : une tranche par run P3 (1+ contrats), pas de tranches imbriquées.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass(frozen=True)
class FreezeBatch:
    batch_id: str
    cycle_id: str | None
    horizon_start: str
    horizon_end: str
    status: str
    decision: str
    total_quantity: float
    contract_count: int
    candidate_count: int
    explanation: str | None
    frozen_at: str
    event_id: int | None


@dataclass(frozen=True)
class FreezeBatchContract:
    batch_id: str
    contract_id: str
    version: int


def _next_batch_id(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT batch_id FROM freeze_batches ORDER BY batch_id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return "FZ-0001"
    last = row["batch_id"]
    try:
        n = int(last.split("-")[-1])
    except (ValueError, IndexError):
        n = 0
    return f"FZ-{n + 1:04d}"


def _row(row: sqlite3.Row) -> FreezeBatch:
    return FreezeBatch(
        batch_id=row["batch_id"],
        cycle_id=row["cycle_id"],
        horizon_start=row["horizon_start"],
        horizon_end=row["horizon_end"],
        status=row["status"],
        decision=row["decision"],
        total_quantity=float(row["total_quantity"]),
        contract_count=int(row["contract_count"]),
        candidate_count=int(row["candidate_count"]),
        explanation=row["explanation"],
        frozen_at=row["frozen_at"],
        event_id=int(row["event_id"]) if row["event_id"] is not None else None,
    )


def create_freeze_batch(
    conn: sqlite3.Connection,
    *,
    contracts: list[tuple[str, int]],
    horizon_start: str,
    horizon_end: str,
    decision: str,
    cycle_id: str | None = None,
    explanation: str | None = None,
    event_id: int | None = None,
) -> FreezeBatch:
    """Crée une tranche gelée immuable référençant les versions figées.

    `contracts` est une liste de tuples (contract_id, version) — les
    versions exactes au moment du freeze sont enregistrées dans
    freeze_batch_contracts.
    """
    if not contracts:
        raise ValueError("Une tranche gelée doit contenir au moins un contrat")

    batch_id = _next_batch_id(conn)

    # Calcul des totaux à partir des versions référencées
    total_qty = 0.0
    cand_count = 0
    for contract_id, version in contracts:
        ver_row = conn.execute(
            "SELECT total_quantity FROM flux_contract_versions "
            "WHERE contract_id = ? AND version = ?",
            (contract_id, version),
        ).fetchone()
        if ver_row is None:
            raise ValueError(
                f"Version {version} du contrat {contract_id} introuvable"
            )
        total_qty += float(ver_row["total_quantity"])
        n_cand = conn.execute(
            "SELECT COUNT(*) AS n FROM flux_contract_links "
            "WHERE contract_id = ? AND version = ?",
            (contract_id, version),
        ).fetchone()["n"]
        cand_count += int(n_cand)

    conn.execute(
        """
        INSERT INTO freeze_batches
            (batch_id, cycle_id, horizon_start, horizon_end, status, decision,
             total_quantity, contract_count, candidate_count, explanation, event_id)
        VALUES (?, ?, ?, ?, 'frozen', ?, ?, ?, ?, ?, ?)
        """,
        (
            batch_id, cycle_id, horizon_start, horizon_end, decision,
            total_qty, len(contracts), cand_count, explanation, event_id,
        ),
    )
    for contract_id, version in contracts:
        conn.execute(
            """
            INSERT INTO freeze_batch_contracts (batch_id, contract_id, version)
            VALUES (?, ?, ?)
            """,
            (batch_id, contract_id, version),
        )

    return _row(
        conn.execute(
            "SELECT * FROM freeze_batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
    )


def fetch_freeze_batch(
    conn: sqlite3.Connection, batch_id: str
) -> FreezeBatch | None:
    row = conn.execute(
        "SELECT * FROM freeze_batches WHERE batch_id = ?", (batch_id,)
    ).fetchone()
    return _row(row) if row else None


def list_freeze_batches(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
) -> list[FreezeBatch]:
    sql = "SELECT * FROM freeze_batches WHERE 1=1"
    params: list[str] = []
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY frozen_at DESC, batch_id ASC"
    return [_row(r) for r in conn.execute(sql, params)]


def get_batch_contracts(
    conn: sqlite3.Connection, batch_id: str
) -> list[FreezeBatchContract]:
    rows = conn.execute(
        """
        SELECT batch_id, contract_id, version
        FROM freeze_batch_contracts WHERE batch_id = ?
        ORDER BY contract_id ASC
        """,
        (batch_id,),
    ).fetchall()
    return [
        FreezeBatchContract(
            batch_id=r["batch_id"],
            contract_id=r["contract_id"],
            version=int(r["version"]),
        )
        for r in rows
    ]


def overlapping_freeze_batches(
    conn: sqlite3.Connection,
    horizon_start: str,
    horizon_end: str,
) -> list[FreezeBatch]:
    """Renvoie les tranches gelées dont l'horizon chevauche la plage donnée."""
    rows = conn.execute(
        """
        SELECT * FROM freeze_batches
        WHERE status = 'frozen'
          AND NOT (date(horizon_end) < date(?) OR date(horizon_start) > date(?))
        ORDER BY frozen_at DESC
        """,
        (horizon_start, horizon_end),
    ).fetchall()
    return [_row(r) for r in rows]


def get_frozen_contract_ids(conn: sqlite3.Connection) -> set[str]:
    """Renvoie l'ensemble des contracts referencés par au moins une tranche."""
    rows = conn.execute(
        "SELECT DISTINCT contract_id FROM freeze_batch_contracts"
    ).fetchall()
    return {r["contract_id"] for r in rows}
