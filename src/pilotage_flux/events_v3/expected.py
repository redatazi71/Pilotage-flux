"""Génération des événements attendus depuis une tranche gelée + lissage.

À partir d'une tranche gelée (freeze_batch) et du lissage du contrat de flux
correspondant, on génère pour chaque candidate les événements attendus :
  - op_start  : début d'opération au workstation routing principal
  - op_finish : fin d'opération (planned = start + qty × unit_time_min)
  - of_close  : clôture OF (après dernière op_finish)

V3 minimal : pas de transferts/contrôles/passages goulot séparés (ils
viendront avec L3.2 si nécessaire). On reste sur les 3 types essentiels
pour calculer des écarts comparables aux événements MES réels OP_STARTED /
OP_FINISHED / OF_CLOSED.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta


EVT_OP_START = "op_start"
EVT_OP_FINISH = "op_finish"
EVT_OF_CLOSE = "of_close"


@dataclass(frozen=True)
class ExpectedEvent:
    expected_event_id: int
    batch_id: str
    contract_id: str
    candidate_id: str
    event_type: str
    sequence_idx: int | None
    workstation_id: str | None
    expected_at: str
    expected_qty: float | None
    matched_actual_id: int | None
    matched_at: str | None


def _row(row: sqlite3.Row) -> ExpectedEvent:
    return ExpectedEvent(
        expected_event_id=int(row["expected_event_id"]),
        batch_id=row["batch_id"],
        contract_id=row["contract_id"],
        candidate_id=row["candidate_id"],
        event_type=row["event_type"],
        sequence_idx=int(row["sequence_idx"]) if row["sequence_idx"] is not None else None,
        workstation_id=row["workstation_id"],
        expected_at=row["expected_at"],
        expected_qty=float(row["expected_qty"]) if row["expected_qty"] is not None else None,
        matched_actual_id=(
            int(row["matched_actual_id"]) if row["matched_actual_id"] is not None else None
        ),
        matched_at=row["matched_at"],
    )


def generate_expected_from_batch(
    conn: sqlite3.Connection, batch_id: str
) -> list[ExpectedEvent]:
    """Génère les événements attendus pour tous les candidates d'une tranche gelée.

    Algorithme :
      Pour chaque (contract, version) de la tranche :
        Pour chaque candidate du contrat :
          Récupère son planned_start dans flux_smoothed_launches (sinon = horizon_start)
          Pour chaque op de son routing (article_id, sequence_idx ASC) :
            Insère op_start (= cumul des durées précédentes)
            Insère op_finish (= op_start + qty × unit_time_min)
          Insère of_close (= dernier op_finish)
    """
    batch = conn.execute(
        "SELECT * FROM freeze_batches WHERE batch_id = ?", (batch_id,)
    ).fetchone()
    if batch is None:
        raise ValueError(f"Tranche gelée inconnue : {batch_id}")

    # Purge les anciens événements attendus pour cette tranche (recompute propre)
    conn.execute(
        "DELETE FROM expected_events WHERE batch_id = ?", (batch_id,)
    )

    contracts = conn.execute(
        """
        SELECT contract_id, version FROM freeze_batch_contracts
        WHERE batch_id = ?
        ORDER BY contract_id ASC
        """,
        (batch_id,),
    ).fetchall()

    horizon_start = batch["horizon_start"]
    # Tolère les dates pures et les datetimes ISO
    try:
        horizon_start_dt = datetime.fromisoformat(horizon_start)
    except ValueError:
        horizon_start_dt = datetime.fromisoformat(horizon_start + "T00:00:00")

    generated_ids: list[int] = []

    for c in contracts:
        cid = c["contract_id"]
        ver = int(c["version"])
        candidates = conn.execute(
            """
            SELECT l.candidate_id, l.qty_in_contract, l.sequence_idx,
                   co.article_id
            FROM flux_contract_links l
            JOIN candidate_orders co ON co.candidate_id = l.candidate_id
            WHERE l.contract_id = ? AND l.version = ?
            ORDER BY l.sequence_idx ASC
            """,
            (cid, ver),
        ).fetchall()

        for cand in candidates:
            cand_id = cand["candidate_id"]
            article_id = cand["article_id"]
            qty = float(cand["qty_in_contract"])

            # Offset de démarrage du candidate (depuis lissage si disponible)
            sm = conn.execute(
                """
                SELECT planned_start FROM flux_smoothed_launches
                WHERE contract_id = ? AND version = ? AND candidate_id = ?
                """,
                (cid, ver, cand_id),
            ).fetchone()
            if sm is not None:
                try:
                    cand_start = datetime.fromisoformat(sm["planned_start"])
                except ValueError:
                    cand_start = horizon_start_dt
            else:
                cand_start = horizon_start_dt

            # Routing du candidate
            ops = conn.execute(
                """
                SELECT sequence_idx, workstation_id, unit_time_min
                FROM routing_operations WHERE article_id = ?
                ORDER BY sequence_idx ASC
                """,
                (article_id,),
            ).fetchall()

            current = cand_start
            for op in ops:
                seq = int(op["sequence_idx"])
                ws = op["workstation_id"]
                unit_t = float(op["unit_time_min"])
                op_duration = timedelta(minutes=qty * unit_t)

                start_at = current
                finish_at = current + op_duration

                cur1 = conn.execute(
                    """
                    INSERT INTO expected_events
                        (batch_id, contract_id, candidate_id, event_type,
                         sequence_idx, workstation_id, expected_at, expected_qty)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (batch_id, cid, cand_id, EVT_OP_START,
                     seq, ws, start_at.isoformat(sep=" "), qty),
                )
                generated_ids.append(cur1.lastrowid)

                cur2 = conn.execute(
                    """
                    INSERT INTO expected_events
                        (batch_id, contract_id, candidate_id, event_type,
                         sequence_idx, workstation_id, expected_at, expected_qty)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (batch_id, cid, cand_id, EVT_OP_FINISH,
                     seq, ws, finish_at.isoformat(sep=" "), qty),
                )
                generated_ids.append(cur2.lastrowid)

                current = finish_at

            # of_close après la dernière op
            cur3 = conn.execute(
                """
                INSERT INTO expected_events
                    (batch_id, contract_id, candidate_id, event_type,
                     expected_at, expected_qty)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (batch_id, cid, cand_id, EVT_OF_CLOSE,
                 current.isoformat(sep=" "), qty),
            )
            generated_ids.append(cur3.lastrowid)

    if not generated_ids:
        return []
    placeholders = ",".join("?" * len(generated_ids))
    rows = conn.execute(
        f"SELECT * FROM expected_events WHERE expected_event_id IN ({placeholders}) "
        f"ORDER BY expected_event_id ASC",
        generated_ids,
    ).fetchall()
    return [_row(r) for r in rows]


def fetch_expected(
    conn: sqlite3.Connection, expected_event_id: int
) -> ExpectedEvent | None:
    row = conn.execute(
        "SELECT * FROM expected_events WHERE expected_event_id = ?",
        (expected_event_id,),
    ).fetchone()
    return _row(row) if row else None


def list_expected(
    conn: sqlite3.Connection,
    *,
    batch_id: str | None = None,
    candidate_id: str | None = None,
    event_type: str | None = None,
    unmatched_only: bool = False,
) -> list[ExpectedEvent]:
    sql = "SELECT * FROM expected_events WHERE 1=1"
    params: list[str] = []
    if batch_id is not None:
        sql += " AND batch_id = ?"
        params.append(batch_id)
    if candidate_id is not None:
        sql += " AND candidate_id = ?"
        params.append(candidate_id)
    if event_type is not None:
        sql += " AND event_type = ?"
        params.append(event_type)
    if unmatched_only:
        sql += " AND matched_actual_id IS NULL"
    sql += " ORDER BY expected_at ASC, expected_event_id ASC"
    return [_row(r) for r in conn.execute(sql, params)]
