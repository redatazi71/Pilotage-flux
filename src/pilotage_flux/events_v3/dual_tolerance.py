"""Filtre dual de tolérances (L3.5 / cadrage §7 bis.4).

Pour une déviation observée :
  1. Récupère le score de magnitude (déjà calculé en L3.2)
  2. Calcule la fréquence = nb de déviations similaires dans une fenêtre
     temporelle paramétrable (data-driven)
  3. Combine score_combined = magnitude × (1 + log(1 + frequency))
  4. Mappe sur un niveau d'action selon des seuils data-driven :
        score < 0.20 → inform
        score < 0.40 → watch
        score < 0.60 → correct_local
        score < 0.80 → replan_local
        score < 1.20 → escalate
        score >= 1.20 → replan_global
  5. Applique une latence (en minutes) avant que l'action ne se déclenche
     pour éviter sur-réactions : triggered_at = decided_at + latency
     (None = en attente)

Tous les seuils + latence + window viennent de la table parameters
(strict data-driven, conforme à la doctrine).
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from pilotage_flux.parameters import get_num


ACTION_INFORM = "inform"
ACTION_WATCH = "watch"
ACTION_CORRECT_LOCAL = "correct_local"
ACTION_REPLAN_LOCAL = "replan_local"
ACTION_ESCALATE = "escalate"
ACTION_REPLAN_GLOBAL = "replan_global"

ACTION_LEVELS = [
    ACTION_INFORM, ACTION_WATCH, ACTION_CORRECT_LOCAL,
    ACTION_REPLAN_LOCAL, ACTION_ESCALATE, ACTION_REPLAN_GLOBAL,
]

DEFAULT_THRESHOLDS = {
    "tolerance_threshold_watch": 0.20,
    "tolerance_threshold_correct_local": 0.40,
    "tolerance_threshold_replan_local": 0.60,
    "tolerance_threshold_escalate": 0.80,
    "tolerance_threshold_replan_global": 1.20,
}
DEFAULT_WINDOW_HOURS = 24
DEFAULT_LATENCY_MINUTES = 0


@dataclass(frozen=True)
class ToleranceDecision:
    decision_id: int
    deviation_id: int
    candidate_id: str | None
    score_magnitude: float
    frequency_in_window: int
    score_combined: float
    action_level: str
    latency_minutes: int
    triggered_at: str | None
    decided_at: str
    source: str = "tolerance"


def _get_thresholds(conn: sqlite3.Connection) -> dict[str, float]:
    out: dict[str, float] = {}
    for name, default in DEFAULT_THRESHOLDS.items():
        v = get_num(conn, scope="global", scope_ref=None, name=name, default=default)
        out[name] = float(v) if v is not None else default
    return out


def _level_from_score(score: float, thresholds: dict[str, float]) -> str:
    if score < thresholds["tolerance_threshold_watch"]:
        return ACTION_INFORM
    if score < thresholds["tolerance_threshold_correct_local"]:
        return ACTION_WATCH
    if score < thresholds["tolerance_threshold_replan_local"]:
        return ACTION_CORRECT_LOCAL
    if score < thresholds["tolerance_threshold_escalate"]:
        return ACTION_REPLAN_LOCAL
    if score < thresholds["tolerance_threshold_replan_global"]:
        return ACTION_ESCALATE
    return ACTION_REPLAN_GLOBAL


def _count_similar_in_window(
    conn: sqlite3.Connection,
    deviation_kind: str,
    candidate_id: str | None,
    window_hours: int,
) -> int:
    """Compte les déviations similaires (même kind, même candidate) détectées
    dans les `window_hours` heures précédentes."""
    cutoff = (
        datetime.utcnow() - timedelta(hours=window_hours)
    ).strftime("%Y-%m-%d %H:%M:%S")
    sql = """
        SELECT COUNT(*) AS n FROM event_deviations
        WHERE deviation_kind = ?
          AND detected_at >= ?
    """
    params: list[str] = [deviation_kind, cutoff]
    if candidate_id is not None:
        sql += " AND candidate_id = ?"
        params.append(candidate_id)
    row = conn.execute(sql, params).fetchone()
    return int(row["n"]) if row else 0


def _row(row: sqlite3.Row) -> ToleranceDecision:
    try:
        source = row["source"] or "tolerance"
    except (KeyError, IndexError):
        source = "tolerance"
    return ToleranceDecision(
        decision_id=int(row["decision_id"]),
        deviation_id=int(row["deviation_id"]),
        candidate_id=row["candidate_id"],
        score_magnitude=float(row["score_magnitude"]),
        frequency_in_window=int(row["frequency_in_window"]),
        score_combined=float(row["score_combined"]),
        action_level=row["action_level"],
        latency_minutes=int(row["latency_minutes"]),
        triggered_at=row["triggered_at"],
        decided_at=row["decided_at"],
        source=source,
    )


def evaluate_dual_tolerance(
    conn: sqlite3.Connection, deviation_id: int
) -> ToleranceDecision:
    """Évalue le filtre dual sur une déviation et persiste la décision.

    Idempotent : si une décision existe déjà pour cette déviation, la retourne.

    V13.C — Si `enable_dual_memory_skip_latency` = 1 et qu'une recette
    apprise (retenue ≥ min_recurrence fois) existe pour ce
    deviation_kind, on court-circuite le filtre dual : décision
    persistée avec `source='memory_shortcut'`, latency_minutes=0.
    """
    existing = conn.execute(
        "SELECT * FROM tolerance_filter_decisions WHERE deviation_id = ?",
        (deviation_id,),
    ).fetchone()
    if existing is not None:
        return _row(existing)

    dev = conn.execute(
        """
        SELECT deviation_id, deviation_kind, candidate_id, score, is_absorbed
        FROM event_deviations WHERE deviation_id = ?
        """,
        (deviation_id,),
    ).fetchone()
    if dev is None:
        raise ValueError(f"Déviation inconnue : {deviation_id}")

    # V13.C — raccourci mémoire (skip latency) si flag activé
    skip_flag = get_num(
        conn, scope="global", scope_ref=None,
        name="enable_dual_memory_skip_latency", default=0.0,
    )
    if skip_flag and float(skip_flag) >= 1.0 and not dev["is_absorbed"]:
        from pilotage_flux.events_v3.dual_memory import try_memory_shortcut
        learned = try_memory_shortcut(conn, deviation_id)
        if learned is not None:
            score = float(dev["score"]) if dev["score"] is not None else 0.0
            cur = conn.execute(
                """
                INSERT INTO tolerance_filter_decisions
                    (deviation_id, candidate_id, score_magnitude,
                     frequency_in_window, score_combined, action_level,
                     latency_minutes, triggered_at, source)
                VALUES (?, ?, ?, 0, ?, ?, 0, datetime('now'),
                        'memory_shortcut')
                """,
                (deviation_id, dev["candidate_id"], score, score, learned),
            )
            row = conn.execute(
                "SELECT * FROM tolerance_filter_decisions "
                "WHERE decision_id = ?",
                (cur.lastrowid,),
            ).fetchone()
            return _row(row)

    if dev["is_absorbed"]:
        # Écart absorbé CPM → action = inform seulement
        action = ACTION_INFORM
        score = float(dev["score"]) if dev["score"] is not None else 0.0
        cur = conn.execute(
            """
            INSERT INTO tolerance_filter_decisions
                (deviation_id, candidate_id, score_magnitude,
                 frequency_in_window, score_combined, action_level,
                 latency_minutes, triggered_at)
            VALUES (?, ?, ?, 0, ?, ?, 0, datetime('now'))
            """,
            (deviation_id, dev["candidate_id"], score, score, action),
        )
        row = conn.execute(
            "SELECT * FROM tolerance_filter_decisions WHERE decision_id = ?",
            (cur.lastrowid,),
        ).fetchone()
        return _row(row)

    thresholds = _get_thresholds(conn)
    window_hours = int(
        get_num(conn, scope="global", scope_ref=None,
                name="tolerance_window_hours",
                default=DEFAULT_WINDOW_HOURS) or DEFAULT_WINDOW_HOURS
    )
    latency_min = int(
        get_num(conn, scope="global", scope_ref=None,
                name="tolerance_latency_minutes",
                default=DEFAULT_LATENCY_MINUTES) or DEFAULT_LATENCY_MINUTES
    )

    score_mag = float(dev["score"]) if dev["score"] is not None else 0.0
    freq = _count_similar_in_window(
        conn, dev["deviation_kind"], dev["candidate_id"], window_hours
    )
    # Composition : magnitude × (1 + log(1 + freq))
    score_combined = score_mag * (1 + math.log1p(freq))
    action = _level_from_score(score_combined, thresholds)

    # Latence : si > 0, triggered_at sera fixé après attente ; en V3 minimal
    # on stocke decided_at + latency_minutes mais on déclenche immédiatement
    # (la simulation temporelle de la latence reste à charge du consommateur)
    if latency_min == 0:
        triggered_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    else:
        triggered_at = None  # NULL = en attente

    cur = conn.execute(
        """
        INSERT INTO tolerance_filter_decisions
            (deviation_id, candidate_id, score_magnitude,
             frequency_in_window, score_combined, action_level,
             latency_minutes, triggered_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (deviation_id, dev["candidate_id"], score_mag, freq,
         score_combined, action, latency_min, triggered_at),
    )
    row = conn.execute(
        "SELECT * FROM tolerance_filter_decisions WHERE decision_id = ?",
        (cur.lastrowid,),
    ).fetchone()
    return _row(row)


def evaluate_all_open_deviations(
    conn: sqlite3.Connection, *, batch_id: str | None = None
) -> list[ToleranceDecision]:
    """Évalue le filtre dual sur toutes les déviations non encore traitées.

    Si batch_id fourni, limite à cette tranche.
    """
    sql = """
        SELECT d.deviation_id FROM event_deviations d
        LEFT JOIN tolerance_filter_decisions t ON t.deviation_id = d.deviation_id
        LEFT JOIN expected_events e ON e.expected_event_id = d.expected_event_id
        WHERE t.decision_id IS NULL
    """
    params: list[str] = []
    if batch_id is not None:
        sql += " AND e.batch_id = ?"
        params.append(batch_id)
    sql += " ORDER BY d.deviation_id ASC"
    rows = conn.execute(sql, params).fetchall()
    return [evaluate_dual_tolerance(conn, int(r["deviation_id"])) for r in rows]


def trigger_pending_decisions(
    conn: sqlite3.Connection, *, now: datetime | None = None
) -> int:
    """Déclenche les décisions dont la latence est écoulée.

    Met triggered_at = now() pour toutes les décisions where
    triggered_at IS NULL AND decided_at + latency_minutes <= now().
    Renvoie le nombre de décisions déclenchées.
    """
    if now is None:
        now = datetime.utcnow()
    cur = conn.execute(
        """
        UPDATE tolerance_filter_decisions
        SET triggered_at = ?
        WHERE triggered_at IS NULL
          AND datetime(decided_at, '+' || latency_minutes || ' minutes') <= ?
        """,
        (now.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d %H:%M:%S")),
    )
    return cur.rowcount


def list_decisions(
    conn: sqlite3.Connection,
    *,
    action_level: str | None = None,
    triggered_only: bool = False,
) -> list[ToleranceDecision]:
    sql = "SELECT * FROM tolerance_filter_decisions WHERE 1=1"
    params: list[str] = []
    if action_level is not None:
        sql += " AND action_level = ?"
        params.append(action_level)
    if triggered_only:
        sql += " AND triggered_at IS NOT NULL"
    sql += " ORDER BY decision_id ASC"
    return [_row(r) for r in conn.execute(sql, params)]
