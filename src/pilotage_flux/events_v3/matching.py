"""Matching attendu/réel et qualification des écarts (L3.2).

Pour chaque tranche gelée + ses événements attendus, on parcourt l'event_store
à la recherche des événements réels MES correspondants (même candidate/OF,
même type d'événement, même sequence_idx).

Quand un match est trouvé, on calcule :
  - delta_time : (actual_occurred_at - expected_at) en minutes
  - delta_qty  : (actual_qty - expected_qty)
  - score      : magnitude normalisée 0..1 (basée sur abs(delta_time) ou ratio qty)

Le score sera utilisé par L3.5 (filtre dual de tolérances).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from pilotage_flux.parameters import get_num


KIND_TIME = "time_delta"
KIND_QTY = "quantity_delta"
KIND_MISSING = "missing_actual"
KIND_UNEXPECTED = "unexpected_actual"
KIND_SCRAP_EXCESS = "qty_scrap_excess"


DEFAULT_TIME_TOLERANCE_MINUTES = 30.0


def _resolve_time_tolerance(conn: sqlite3.Connection) -> float:
    """Lit le seuil de tolérance temporelle (en minutes) depuis parameters.

    Permet aux études et runs réels d'ajuster la sensibilité du matching
    sans modifier le code. Le nom de paramètre est `matching_time_tolerance_minutes`.
    """
    val = get_num(
        conn, scope="global", scope_ref=None,
        name="matching_time_tolerance_minutes",
        default=DEFAULT_TIME_TOLERANCE_MINUTES,
    )
    return float(val) if val is not None else DEFAULT_TIME_TOLERANCE_MINUTES


@dataclass(frozen=True)
class Deviation:
    deviation_id: int
    expected_event_id: int | None
    actual_event_id: int | None
    candidate_id: str
    deviation_kind: str
    delta_value: float | None
    score: float | None
    cpm_margin_used: float | None
    is_absorbed: bool
    qualification: str | None


# Mapping event_type MES réel -> event_type attendu V3
ACTUAL_TO_EXPECTED = {
    "OP_STARTED": "op_start",
    "OP_FINISHED": "op_finish",
    "OF_CLOSED": "of_close",
}


def _score_time_delta(delta_minutes: float, tolerance_minutes: float = 30.0) -> float:
    """Magnitude normalisée 0..1.

    delta = 0 -> score 0, delta = tolerance -> score 1, delta > tolerance -> 1.
    Le seuil tolerance_minutes sera paramétrable en L3.5 via la table parameters.
    """
    if tolerance_minutes <= 0:
        return 1.0 if delta_minutes != 0 else 0.0
    return min(1.0, abs(delta_minutes) / tolerance_minutes)


def _score_qty_delta(actual: float, expected: float) -> float:
    """Magnitude normalisée 0..1 sur le ratio écart/attendu."""
    if expected <= 0:
        return 1.0 if actual != 0 else 0.0
    return min(1.0, abs(actual - expected) / expected)


def _qualify(score: float) -> str:
    if score < 0.2:
        return "low"
    if score < 0.5:
        return "medium"
    if score < 0.8:
        return "high"
    return "critical"


def _row(row: sqlite3.Row) -> Deviation:
    return Deviation(
        deviation_id=int(row["deviation_id"]),
        expected_event_id=(
            int(row["expected_event_id"]) if row["expected_event_id"] is not None else None
        ),
        actual_event_id=(
            int(row["actual_event_id"]) if row["actual_event_id"] is not None else None
        ),
        candidate_id=row["candidate_id"],
        deviation_kind=row["deviation_kind"],
        delta_value=(
            float(row["delta_value"]) if row["delta_value"] is not None else None
        ),
        score=float(row["score"]) if row["score"] is not None else None,
        cpm_margin_used=(
            float(row["cpm_margin_used"]) if row["cpm_margin_used"] is not None else None
        ),
        is_absorbed=bool(row["is_absorbed"]),
        qualification=row["qualification"],
    )


def _candidate_of_actual(
    conn: sqlite3.Connection, actual_row: sqlite3.Row
) -> str | None:
    """Remonte du manufacturing_order vers son candidate (V3 simple = 1:1)."""
    if actual_row["aggregate_type"] != "manufacturing_order":
        return None
    row = conn.execute(
        "SELECT candidate_id FROM manufacturing_orders WHERE of_id = ?",
        (actual_row["aggregate_id"],),
    ).fetchone()
    return row["candidate_id"] if row else None


def match_actuals_to_expected(
    conn: sqlite3.Connection, batch_id: str
) -> list[Deviation]:
    """Matche les événements MES réels aux événements attendus d'une tranche.

    Algorithme V3 simple :
      1. Charge tous les expected_events de la tranche, non matchés
      2. Pour chaque type d'event attendu (op_start/op_finish/of_close) :
         - Cherche dans event_store les events réels du même type (mappé)
           pour un OF rattaché au candidate (via manufacturing_orders.candidate_id)
         - Si plusieurs op_start/op_finish, on les apparie dans l'ordre
           chronologique (1er attendu ↔ 1er réel par sequence_idx)
      3. Pour chaque paire (expected, actual), calcule delta_time
      4. Persiste un event_deviations + marque expected.matched_actual_id

    Renvoie la liste des deviations créées dans cet appel.
    """
    expected_rows = conn.execute(
        """
        SELECT * FROM expected_events
        WHERE batch_id = ? AND matched_actual_id IS NULL
        ORDER BY candidate_id ASC, expected_at ASC, expected_event_id ASC
        """,
        (batch_id,),
    ).fetchall()
    if not expected_rows:
        return []

    new_deviations: list[int] = []

    # Index attendus par (candidate_id, mes_event_type, sequence_idx)
    expected_by_key: dict[tuple, list[sqlite3.Row]] = {}
    for er in expected_rows:
        # Map V3 expected -> V0 actual type
        mes_type = {
            "op_start": "OP_STARTED",
            "op_finish": "OP_FINISHED",
            "of_close": "OF_CLOSED",
        }.get(er["event_type"])
        if mes_type is None:
            continue
        key = (er["candidate_id"], mes_type, er["sequence_idx"])
        expected_by_key.setdefault(key, []).append(er)

    for (cand_id, mes_type, seq), bucket in expected_by_key.items():
        # Cherche actuals du même candidate et même mes_type
        actuals = conn.execute(
            """
            SELECT es.event_id, es.occurred_at, es.event_type, es.payload_json,
                   es.aggregate_type, es.aggregate_id
            FROM event_store es
            JOIN manufacturing_orders mo ON mo.of_id = es.aggregate_id
            WHERE es.aggregate_type = 'manufacturing_order'
              AND es.event_type = ?
              AND mo.candidate_id = ?
            ORDER BY es.event_id ASC
            """,
            (mes_type, cand_id),
        ).fetchall()

        # Filtre par sequence_idx pour OP_STARTED/OP_FINISHED (lit le payload)
        if mes_type in ("OP_STARTED", "OP_FINISHED") and seq is not None:
            filtered: list[sqlite3.Row] = []
            for a in actuals:
                try:
                    payload = json.loads(a["payload_json"] or "{}")
                except (ValueError, TypeError):
                    payload = {}
                if int(payload.get("sequence_idx", -1)) == int(seq):
                    filtered.append(a)
            actuals = filtered

        tolerance_minutes = _resolve_time_tolerance(conn)
        # Apparie 1:1 dans l'ordre
        for expected_row, actual_row in zip(bucket, actuals):
            try:
                expected_dt = datetime.fromisoformat(expected_row["expected_at"])
                actual_dt = datetime.fromisoformat(actual_row["occurred_at"])
            except (ValueError, TypeError):
                continue
            delta_min = (actual_dt - expected_dt).total_seconds() / 60
            score = _score_time_delta(delta_min, tolerance_minutes)
            qualification = _qualify(score)

            cur = conn.execute(
                """
                INSERT INTO event_deviations
                    (expected_event_id, actual_event_id, candidate_id,
                     deviation_kind, delta_value, score, qualification)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    expected_row["expected_event_id"],
                    actual_row["event_id"],
                    cand_id,
                    KIND_TIME,
                    delta_min,
                    score,
                    qualification,
                ),
            )
            new_deviations.append(cur.lastrowid)

            conn.execute(
                """
                UPDATE expected_events
                SET matched_actual_id = ?, matched_at = datetime('now')
                WHERE expected_event_id = ?
                """,
                (actual_row["event_id"], expected_row["expected_event_id"]),
            )

    if not new_deviations:
        return []
    placeholders = ",".join("?" * len(new_deviations))
    rows = conn.execute(
        f"SELECT * FROM event_deviations WHERE deviation_id IN ({placeholders}) "
        f"ORDER BY deviation_id ASC",
        new_deviations,
    ).fetchall()
    return [_row(r) for r in rows]


def list_deviations(
    conn: sqlite3.Connection,
    *,
    candidate_id: str | None = None,
    deviation_kind: str | None = None,
    min_score: float | None = None,
) -> list[Deviation]:
    sql = "SELECT * FROM event_deviations WHERE 1=1"
    params: list[str | float] = []
    if candidate_id is not None:
        sql += " AND candidate_id = ?"
        params.append(candidate_id)
    if deviation_kind is not None:
        sql += " AND deviation_kind = ?"
        params.append(deviation_kind)
    if min_score is not None:
        sql += " AND score >= ?"
        params.append(min_score)
    sql += " ORDER BY deviation_id ASC"
    return [_row(r) for r in conn.execute(sql, params)]
