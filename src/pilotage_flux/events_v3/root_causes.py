"""Causes racines pondérées bayésiennes (L3.4 / cadrage §18).

Pour chaque déviation non absorbée, on propose les causes candidates
applicables (filtrées par applies_to_kind). Le score initial =
weight × confidence (prior). À chaque confirmation manuelle ou évidence
contradictoire, la confidence est mise à jour par règle bayésienne
simple : P(cause|evidence) ∝ P(evidence|cause) × P(cause).

V3 minimal : confidence et weight stockés en table, mise à jour par
fonction `update_confidence_after_evidence()`. Pas de calcul bayésien
multi-variable plein — c'est volontairement simple, étendable.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class RootCauseRule:
    rule_id: str
    cause: str
    label: str
    weight: float
    confidence: float
    domain: str | None
    applies_to_kind: str | None
    version: int


@dataclass(frozen=True)
class CauseAttachment:
    attach_id: int
    deviation_id: int
    rule_id: str
    rule_version: int
    score: float
    posterior: float | None
    confirmed: bool
    explanation: str | None
    attached_at: str


def _row_rule(row: sqlite3.Row) -> RootCauseRule:
    return RootCauseRule(
        rule_id=row["rule_id"],
        cause=row["cause"],
        label=row["label"],
        weight=float(row["weight"]),
        confidence=float(row["confidence"]),
        domain=row["domain"],
        applies_to_kind=row["applies_to_kind"],
        version=int(row["version"]),
    )


def _row_attach(row: sqlite3.Row) -> CauseAttachment:
    return CauseAttachment(
        attach_id=int(row["attach_id"]),
        deviation_id=int(row["deviation_id"]),
        rule_id=row["rule_id"],
        rule_version=int(row["rule_version"]),
        score=float(row["score"]),
        posterior=float(row["posterior"]) if row["posterior"] is not None else None,
        confirmed=bool(row["confirmed"]),
        explanation=row["explanation"],
        attached_at=row["attached_at"],
    )


def list_active_rules(
    conn: sqlite3.Connection, *, applies_to_kind: str | None = None
) -> list[RootCauseRule]:
    """Charge les causes racines actives (valid_to IS NULL), filtrable par kind."""
    sql = "SELECT * FROM root_cause_rules WHERE valid_to IS NULL"
    params: list[str] = []
    if applies_to_kind is not None:
        sql += " AND (applies_to_kind = ? OR applies_to_kind IS NULL)"
        params.append(applies_to_kind)
    sql += " ORDER BY rule_id ASC, version DESC"
    seen: set[str] = set()
    out: list[RootCauseRule] = []
    for r in conn.execute(sql, params):
        if r["rule_id"] in seen:
            continue
        seen.add(r["rule_id"])
        out.append(_row_rule(r))
    return out


def attach_causes_to_deviation(
    conn: sqlite3.Connection,
    deviation_id: int,
    *,
    only_kind: bool = True,
) -> list[CauseAttachment]:
    """Attache les causes candidates applicables à une déviation.

    Si only_kind=True, ne propose que les causes dont applies_to_kind
    correspond au deviation_kind. Sinon, toutes les causes actives.

    Score initial = weight × confidence (prior). Idempotent : on n'attache
    pas deux fois la même rule_id.
    """
    dev = conn.execute(
        "SELECT deviation_kind, is_absorbed FROM event_deviations WHERE deviation_id = ?",
        (deviation_id,),
    ).fetchone()
    if dev is None:
        raise ValueError(f"Déviation inconnue : {deviation_id}")
    if dev["is_absorbed"]:
        # Écart absorbé CPM : pas de cause racine à proposer
        return []

    kind = dev["deviation_kind"] if only_kind else None
    rules = list_active_rules(conn, applies_to_kind=kind)

    # Existants pour idempotence
    existing = {
        r["rule_id"]
        for r in conn.execute(
            "SELECT rule_id FROM event_deviation_causes WHERE deviation_id = ?",
            (deviation_id,),
        )
    }

    new_ids: list[int] = []
    for rule in rules:
        if rule.rule_id in existing:
            continue
        score = rule.weight * rule.confidence
        cur = conn.execute(
            """
            INSERT INTO event_deviation_causes
                (deviation_id, rule_id, rule_version, score, posterior)
            VALUES (?, ?, ?, ?, ?)
            """,
            (deviation_id, rule.rule_id, rule.version, score, score),
        )
        new_ids.append(cur.lastrowid)

    if not new_ids:
        return []
    placeholders = ",".join("?" * len(new_ids))
    rows = conn.execute(
        f"SELECT * FROM event_deviation_causes WHERE attach_id IN ({placeholders}) "
        f"ORDER BY score DESC, attach_id ASC",
        new_ids,
    ).fetchall()
    return [_row_attach(r) for r in rows]


def confirm_cause(
    conn: sqlite3.Connection,
    attach_id: int,
    *,
    explanation: str | None = None,
) -> CauseAttachment:
    """Confirme manuellement une cause attachée et met à jour la confidence
    bayésienne de la règle source (P(cause|evidence) augmente).

    Règle bayésienne simple : nouvelle_confidence = min(1, confidence * (1 + alpha))
    avec alpha = 0.2 (paramétrable). Idempotent : ne ré-update pas si déjà confirmé.
    """
    row = conn.execute(
        "SELECT * FROM event_deviation_causes WHERE attach_id = ?",
        (attach_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Attachement inconnu : {attach_id}")
    if row["confirmed"]:
        return _row_attach(row)

    conn.execute(
        """
        UPDATE event_deviation_causes
        SET confirmed = 1,
            explanation = COALESCE(?, explanation)
        WHERE attach_id = ?
        """,
        (explanation, attach_id),
    )

    # Mise à jour bayésienne de la confidence de la règle
    rule = conn.execute(
        "SELECT confidence FROM root_cause_rules "
        "WHERE rule_id = ? AND version = ?",
        (row["rule_id"], row["rule_version"]),
    ).fetchone()
    if rule is not None:
        new_conf = min(1.0, float(rule["confidence"]) * 1.2)
        conn.execute(
            "UPDATE root_cause_rules SET confidence = ? "
            "WHERE rule_id = ? AND version = ?",
            (new_conf, row["rule_id"], row["rule_version"]),
        )
        # Update posterior de cette attache
        weight = conn.execute(
            "SELECT weight FROM root_cause_rules "
            "WHERE rule_id = ? AND version = ?",
            (row["rule_id"], row["rule_version"]),
        ).fetchone()["weight"]
        conn.execute(
            "UPDATE event_deviation_causes SET posterior = ? WHERE attach_id = ?",
            (float(weight) * new_conf, attach_id),
        )

    new_row = conn.execute(
        "SELECT * FROM event_deviation_causes WHERE attach_id = ?", (attach_id,)
    ).fetchone()
    return _row_attach(new_row)


def list_causes_for_deviation(
    conn: sqlite3.Connection, deviation_id: int
) -> list[CauseAttachment]:
    rows = conn.execute(
        "SELECT * FROM event_deviation_causes WHERE deviation_id = ? "
        "ORDER BY score DESC, attach_id ASC",
        (deviation_id,),
    ).fetchall()
    return [_row_attach(r) for r in rows]


def top_causes_across_deviations(
    conn: sqlite3.Connection,
    *,
    limit: int = 5,
    only_confirmed: bool = False,
) -> list[dict]:
    """Aggrège les causes les plus fréquentes/scorées sur l'ensemble des déviations.

    Renvoie une liste de dicts {rule_id, cause, label, count, total_score}.
    Utile pour le filtre dual de tolérances (L3.5) qui détecte les patterns
    récurrents.
    """
    sql = """
        SELECT
            r.rule_id, r.cause, r.label,
            COUNT(*) AS count,
            SUM(c.score) AS total_score,
            AVG(c.score) AS avg_score
        FROM event_deviation_causes c
        JOIN root_cause_rules r ON r.rule_id = c.rule_id AND r.version = c.rule_version
        WHERE 1=1
    """
    if only_confirmed:
        sql += " AND c.confirmed = 1"
    sql += " GROUP BY r.rule_id, r.cause, r.label ORDER BY total_score DESC"
    rows = conn.execute(sql).fetchall()
    return [
        {
            "rule_id": r["rule_id"],
            "cause": r["cause"],
            "label": r["label"],
            "count": int(r["count"]),
            "total_score": float(r["total_score"]),
            "avg_score": float(r["avg_score"]),
        }
        for r in rows[:limit]
    ]
