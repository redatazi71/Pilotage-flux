"""Filtre dual de mémoire P4 (L3.6 / cadrage §7 bis.5).

Lors de la clôture P4 d'un OF, on capture la « recette » :
  signature = (deviation_kind, top_cause, action_level)
  outcome  = success | failure | partial

Deux scores :
  - significance : test statistique simple sur le score de magnitude
    (K-S simplifié : ratio entre score moyen et écart-type historique)
  - recurrence   : fréquence d'apparition de cette signature dans les
    recettes passées (compteur normalisé sur taille historique)

Score combiné = (significance + recurrence) / 2.
Si score_combined >= seuil_apprentissage (data-driven, default 0.5) :
  - is_retained = 1
  - mise à jour bayésienne possible d'un seuil (ex : tolerance_threshold_*,
    cpm_margin_minutes) — V3 minimal : pas d'auto-update implicite,
    on enregistre la décision update_rule pour validation manuelle.

C'est le score qui détermine si la recette devient réutilisable.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

from pilotage_flux.parameters import get_num


DEFAULT_LEARN_THRESHOLD = 0.5


@dataclass(frozen=True)
class MemoryRecipe:
    recipe_id: int
    of_id: str | None
    candidate_id: str | None
    deviation_signature: str
    deviation_kind: str | None
    cause_rule_id: str | None
    action_level: str | None
    outcome: str | None
    score_significance: float | None
    score_recurrence: float | None
    score_combined: float | None
    is_retained: bool
    retention_reason: str | None
    created_at: str


@dataclass(frozen=True)
class MemoryDecision:
    decision_id: int
    recipe_id: int
    decision: str
    target_rule_id: str | None
    parameter_updated: str | None
    old_value: float | None
    new_value: float | None
    explanation: str | None


def _row_recipe(row: sqlite3.Row) -> MemoryRecipe:
    return MemoryRecipe(
        recipe_id=int(row["recipe_id"]),
        of_id=row["of_id"],
        candidate_id=row["candidate_id"],
        deviation_signature=row["deviation_signature"],
        deviation_kind=row["deviation_kind"],
        cause_rule_id=row["cause_rule_id"],
        action_level=row["action_level"],
        outcome=row["outcome"],
        score_significance=(
            float(row["score_significance"]) if row["score_significance"] is not None else None
        ),
        score_recurrence=(
            float(row["score_recurrence"]) if row["score_recurrence"] is not None else None
        ),
        score_combined=(
            float(row["score_combined"]) if row["score_combined"] is not None else None
        ),
        is_retained=bool(row["is_retained"]),
        retention_reason=row["retention_reason"],
        created_at=row["created_at"],
    )


def _row_decision(row: sqlite3.Row) -> MemoryDecision:
    return MemoryDecision(
        decision_id=int(row["decision_id"]),
        recipe_id=int(row["recipe_id"]),
        decision=row["decision"],
        target_rule_id=row["target_rule_id"],
        parameter_updated=row["parameter_updated"],
        old_value=float(row["old_value"]) if row["old_value"] is not None else None,
        new_value=float(row["new_value"]) if row["new_value"] is not None else None,
        explanation=row["explanation"],
    )


def _make_signature(
    deviation_kind: str | None,
    top_cause: str | None,
    action_level: str | None,
) -> str:
    return f"{deviation_kind or '-'}|{top_cause or '-'}|{action_level or '-'}"


def _score_significance(
    conn: sqlite3.Connection, deviation_kind: str | None
) -> float:
    """Significativité = ratio score moyen / (écart-type + 1) sur l'historique.

    V3 minimal : approximation simple. La formule exacte K-S sera ajoutée
    si nécessaire en consolidation.
    """
    if deviation_kind is None:
        return 0.5
    row = conn.execute(
        "SELECT AVG(score) AS m, COUNT(*) AS n FROM event_deviations "
        "WHERE deviation_kind = ? AND score IS NOT NULL",
        (deviation_kind,),
    ).fetchone()
    if not row or int(row["n"]) == 0:
        return 0.5
    mean = float(row["m"] or 0)
    # On normalise sur 0..1 via mean lui-même (les scores étant déjà 0..1)
    return min(1.0, mean)


def _score_recurrence(
    conn: sqlite3.Connection, signature: str
) -> float:
    """Récurrence = nb d'occurrences passées de cette signature / log(1 + total).

    Plus la signature revient, plus le score monte vers 1 (avec saturation).
    """
    total_row = conn.execute(
        "SELECT COUNT(*) AS n FROM memory_recipes"
    ).fetchone()
    total = int(total_row["n"]) if total_row else 0
    if total == 0:
        return 0.0
    occ_row = conn.execute(
        "SELECT COUNT(*) AS n FROM memory_recipes WHERE deviation_signature = ?",
        (signature,),
    ).fetchone()
    occ = int(occ_row["n"]) if occ_row else 0
    return min(1.0, occ / math.log1p(total + 1))


def capture_recipe(
    conn: sqlite3.Connection,
    *,
    of_id: str,
    outcome: str = "success",
) -> tuple[MemoryRecipe, MemoryDecision]:
    """Capture la recette à la clôture P4 d'un OF + applique le filtre dual.

    Recherche dans event_deviations / event_deviation_causes / tolerance_filter_decisions
    la combinaison principale (déviation max-score) pour le candidate de cet OF.
    Calcule signature + scores + decide retenue.

    Retourne (recipe, decision).
    """
    of_row = conn.execute(
        "SELECT candidate_id, status FROM manufacturing_orders WHERE of_id = ?",
        (of_id,),
    ).fetchone()
    if of_row is None:
        raise ValueError(f"OF inconnu : {of_id}")
    if of_row["status"] != "closed":
        raise ValueError(
            f"OF {of_id} en statut {of_row['status']!r} : capture P4 attend 'closed'"
        )

    candidate_id = of_row["candidate_id"]

    # Trouve la déviation top-score pour ce candidate
    top_dev = conn.execute(
        """
        SELECT d.deviation_id, d.deviation_kind, d.score
        FROM event_deviations d
        WHERE d.candidate_id = ?
        ORDER BY COALESCE(d.score, 0) DESC, d.deviation_id ASC
        LIMIT 1
        """,
        (candidate_id,),
    ).fetchone()

    if top_dev is None:
        # Aucune déviation : recette neutre, log_only
        sig = _make_signature(None, None, None)
        cur = conn.execute(
            """
            INSERT INTO memory_recipes
                (of_id, candidate_id, deviation_signature, outcome,
                 score_significance, score_recurrence, score_combined,
                 is_retained, retention_reason)
            VALUES (?, ?, ?, ?, 0.0, 0.0, 0.0, 0, 'no_deviation')
            """,
            (of_id, candidate_id, sig, outcome),
        )
        recipe_id = cur.lastrowid
        dec_cur = conn.execute(
            """
            INSERT INTO memory_filter_decisions
                (recipe_id, decision, explanation)
            VALUES (?, 'log_only', 'Aucune deviation observee')
            """,
            (recipe_id,),
        )
        recipe = _row_recipe(
            conn.execute(
                "SELECT * FROM memory_recipes WHERE recipe_id = ?", (recipe_id,)
            ).fetchone()
        )
        decision = _row_decision(
            conn.execute(
                "SELECT * FROM memory_filter_decisions WHERE decision_id = ?",
                (dec_cur.lastrowid,),
            ).fetchone()
        )
        return recipe, decision

    # Top cause attachée (par score)
    top_cause = conn.execute(
        """
        SELECT rule_id FROM event_deviation_causes
        WHERE deviation_id = ?
        ORDER BY score DESC, attach_id ASC LIMIT 1
        """,
        (top_dev["deviation_id"],),
    ).fetchone()
    cause_rule_id = top_cause["rule_id"] if top_cause else None

    # Action_level associé à cette déviation (filtre dual de tolérances)
    tol = conn.execute(
        """
        SELECT action_level FROM tolerance_filter_decisions
        WHERE deviation_id = ?
        ORDER BY decision_id ASC LIMIT 1
        """,
        (top_dev["deviation_id"],),
    ).fetchone()
    action_level = tol["action_level"] if tol else None

    signature = _make_signature(top_dev["deviation_kind"], cause_rule_id, action_level)
    significance = _score_significance(conn, top_dev["deviation_kind"])
    recurrence = _score_recurrence(conn, signature)
    combined = (significance + recurrence) / 2.0

    threshold = float(
        get_num(
            conn,
            scope="global",
            scope_ref=None,
            name="memory_learning_threshold",
            default=DEFAULT_LEARN_THRESHOLD,
        )
        or DEFAULT_LEARN_THRESHOLD
    )
    is_retained = combined >= threshold
    reason = (
        f"score {combined:.2f} >= seuil {threshold:.2f}"
        if is_retained
        else f"score {combined:.2f} < seuil {threshold:.2f}"
    )

    cur = conn.execute(
        """
        INSERT INTO memory_recipes
            (of_id, candidate_id, deviation_signature, deviation_kind,
             cause_rule_id, action_level, outcome,
             score_significance, score_recurrence, score_combined,
             is_retained, retention_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (of_id, candidate_id, signature, top_dev["deviation_kind"],
         cause_rule_id, action_level, outcome,
         significance, recurrence, combined,
         1 if is_retained else 0, reason),
    )
    recipe_id = cur.lastrowid

    decision_label = "retain" if is_retained else "log_only"
    dec_cur = conn.execute(
        """
        INSERT INTO memory_filter_decisions
            (recipe_id, decision, target_rule_id, explanation)
        VALUES (?, ?, ?, ?)
        """,
        (recipe_id, decision_label, cause_rule_id, reason),
    )

    recipe = _row_recipe(
        conn.execute(
            "SELECT * FROM memory_recipes WHERE recipe_id = ?", (recipe_id,)
        ).fetchone()
    )
    decision = _row_decision(
        conn.execute(
            "SELECT * FROM memory_filter_decisions WHERE decision_id = ?",
            (dec_cur.lastrowid,),
        ).fetchone()
    )
    return recipe, decision


def update_parameter_from_learning(
    conn: sqlite3.Connection,
    recipe_id: int,
    *,
    parameter_name: str,
    new_value: float,
    explanation: str | None = None,
) -> MemoryDecision:
    """Met à jour explicitement un paramètre data-driven depuis une recette retenue.

    Trace dans memory_filter_decisions (decision='update_rule') avec old/new values.
    """
    recipe = conn.execute(
        "SELECT is_retained FROM memory_recipes WHERE recipe_id = ?",
        (recipe_id,),
    ).fetchone()
    if recipe is None:
        raise ValueError(f"Recipe inconnue : {recipe_id}")
    if not bool(recipe["is_retained"]):
        raise ValueError(
            f"Recipe {recipe_id} non retenue (is_retained=0) : pas de mise a jour autorisee"
        )

    # Lit l'ancienne valeur
    old_row = conn.execute(
        "SELECT value_num FROM parameters "
        "WHERE name = ? AND valid_to IS NULL "
        "ORDER BY version DESC LIMIT 1",
        (parameter_name,),
    ).fetchone()
    old_value = float(old_row["value_num"]) if old_row and old_row["value_num"] is not None else None

    if old_row is None:
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, ?, ?)",
            (parameter_name, new_value),
        )
    else:
        conn.execute(
            "UPDATE parameters SET value_num = ? "
            "WHERE name = ? AND valid_to IS NULL",
            (new_value, parameter_name),
        )

    cur = conn.execute(
        """
        INSERT INTO memory_filter_decisions
            (recipe_id, decision, parameter_updated, old_value, new_value, explanation)
        VALUES (?, 'update_rule', ?, ?, ?, ?)
        """,
        (recipe_id, parameter_name, old_value, new_value, explanation),
    )
    return _row_decision(
        conn.execute(
            "SELECT * FROM memory_filter_decisions WHERE decision_id = ?",
            (cur.lastrowid,),
        ).fetchone()
    )


def list_recipes(
    conn: sqlite3.Connection,
    *,
    retained_only: bool = False,
    signature: str | None = None,
) -> list[MemoryRecipe]:
    sql = "SELECT * FROM memory_recipes WHERE 1=1"
    params: list[str] = []
    if retained_only:
        sql += " AND is_retained = 1"
    if signature is not None:
        sql += " AND deviation_signature = ?"
        params.append(signature)
    sql += " ORDER BY recipe_id ASC"
    return [_row_recipe(r) for r in conn.execute(sql, params)]


def list_memory_decisions(
    conn: sqlite3.Connection,
    *,
    recipe_id: int | None = None,
    decision: str | None = None,
) -> list[MemoryDecision]:
    sql = "SELECT * FROM memory_filter_decisions WHERE 1=1"
    params: list[str | int] = []
    if recipe_id is not None:
        sql += " AND recipe_id = ?"
        params.append(recipe_id)
    if decision is not None:
        sql += " AND decision = ?"
        params.append(decision)
    sql += " ORDER BY decision_id ASC"
    return [_row_decision(r) for r in conn.execute(sql, params)]


# --- V13.C — Mémoire ACTIVE : réutilisation des recettes retenues -----

def lookup_retained_signature(
    conn: sqlite3.Connection,
    deviation_kind: str | None,
    cause_rule_id: str | None,
    action_level: str | None,
) -> MemoryRecipe | None:
    """V13.C — Cherche dans `memory_recipes` une recette **retenue**
    (`is_retained = 1`) avec exactement la même signature
    (deviation_kind, cause_rule_id, action_level).

    Renvoie la recette la plus récente correspondante, ou None.

    Permet de transformer la mémoire passive en signal actif :
    un appelant (ex. evaluate_dual_tolerance) peut décider d'agir
    sans latence si une recette retenue prédit une action efficace.
    """
    sig = _make_signature(deviation_kind, cause_rule_id, action_level)
    row = conn.execute(
        """
        SELECT * FROM memory_recipes
        WHERE deviation_signature = ? AND is_retained = 1
        ORDER BY recipe_id DESC
        LIMIT 1
        """,
        (sig,),
    ).fetchone()
    return _row_recipe(row) if row else None
