"""MACRS A.5 — Pareto hiérarchique.

Référence : matrice_operationnelle_specification.md §1.3, §2.5, §8.

Le Pareto hiérarchique est le consommateur principal de la Couche 2
opérationnelle. Il aggrège les cellules ACTIVE sur la fenêtre
W_courte (30 j) pour produire :

  - Pareto par **racine** (R001..R046) : qui pèse le plus aujourd'hui ?
  - Pareto par **catégorie Δ** (Mat/Cap/Op/Qual/Temp/Info/Sync) :
    quelle nature d'écart domine ?
  - Drill-down racines au sein d'une catégorie
  - Détection des **racines émergentes** (ratio W_courte/W_longue > θ)
    et déclinantes (< 1/θ)
  - Criticité combinée (fréquence × impact moyen) — Option D

Tous les indicateurs sont **calculés à la demande** sur les
agrégats bruts (spec §2.5). Le découplage stockage/calcul permet
de modifier formules / coefficients sans recalculer l'historique.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pilotage_flux.cybernetic.macrs.couche2 import (
    STATUS_ACTIVE,
    W_COURTE_DAYS,
    W_LONGUE_DAYS,
    window_lower_bound,
)


# ---------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class ParetoRacineEntry:
    racine_id: str
    domaine: str
    label: str
    predictibilite: str
    n_events_w_courte: int
    n_events_w_longue: int
    impact_pondere: float          # somme impact_score W_courte
    n_cells_active: int             # nb catégories Δ actives
    ratio_emergence: float | None   # W_courte / W_longue


@dataclass(frozen=True)
class ParetoCategoryEntry:
    categorie_code: str
    label: str
    n_events_w_courte: int
    impact_pondere: float
    n_racines_active: int


@dataclass(frozen=True)
class EmergingRacine:
    racine_id: str
    label: str
    domaine: str
    n_w_courte: int
    n_w_longue: int
    ratio_emergence: float


@dataclass(frozen=True)
class CriticityEntry:
    racine_id: str
    label: str
    frequency_per_day: float
    impact_mean: float
    criticite: float                # frequency × impact_mean


# ---------------------------------------------------------------------
# Pareto racines
# ---------------------------------------------------------------------


def pareto_racines(
    conn: sqlite3.Connection,
    *,
    now_iso: str,
    top_k: int | None = None,
) -> list[ParetoRacineEntry]:
    """Aggrège la matrice opérationnelle par racine.

    Une racine entre dans le Pareto si elle a au moins une cellule
    ACTIVE. L'impact pondéré est la somme des `impact_score` des
    événements W_courte de toutes ses cellules actives.

    Trié par impact pondéré décroissant.
    """
    lower_c = window_lower_bound(now_iso, W_COURTE_DAYS)
    lower_l = window_lower_bound(now_iso, W_LONGUE_DAYS)
    rows = conn.execute(
        """
        SELECT r.racine_id, r.domaine, r.label, r.predictibilite,
               COUNT(DISTINCT cc.cell_id) AS n_cells_active,
               COALESCE(SUM(
                   CASE WHEN ce.occurred_at >= ? THEN 1 ELSE 0 END
               ), 0) AS n_w_courte,
               COALESCE(SUM(
                   CASE WHEN ce.occurred_at >= ? THEN 1 ELSE 0 END
               ), 0) AS n_w_longue,
               COALESCE(SUM(
                   CASE WHEN ce.occurred_at >= ?
                        THEN COALESCE(ce.impact_score, 0)
                        ELSE 0 END
               ), 0.0) AS impact_pondere
        FROM macrs_racines r
        JOIN causal_cells cc ON cc.racine_id = r.racine_id
        LEFT JOIN causal_events ce ON ce.cell_id = cc.cell_id
        WHERE cc.status = ?
        GROUP BY r.racine_id, r.domaine, r.label, r.predictibilite
        ORDER BY impact_pondere DESC, n_w_courte DESC
        """,
        (lower_c, lower_l, lower_c, STATUS_ACTIVE),
    ).fetchall()

    entries: list[ParetoRacineEntry] = []
    for r in rows:
        n_c = int(r["n_w_courte"])
        n_l = int(r["n_w_longue"])
        ratio = (n_c / n_l) if n_l > 0 else None
        entries.append(ParetoRacineEntry(
            racine_id=r["racine_id"],
            domaine=r["domaine"],
            label=r["label"],
            predictibilite=r["predictibilite"],
            n_events_w_courte=n_c,
            n_events_w_longue=n_l,
            impact_pondere=float(r["impact_pondere"]),
            n_cells_active=int(r["n_cells_active"]),
            ratio_emergence=ratio,
        ))

    if top_k is not None:
        entries = entries[:top_k]
    return entries


# ---------------------------------------------------------------------
# Pareto catégories Δ
# ---------------------------------------------------------------------


def pareto_categories(
    conn: sqlite3.Connection,
    *,
    now_iso: str,
    top_k: int | None = None,
) -> list[ParetoCategoryEntry]:
    """Aggrège par catégorie Δ sur les cellules ACTIVE."""
    lower_c = window_lower_bound(now_iso, W_COURTE_DAYS)
    rows = conn.execute(
        """
        SELECT cat.categorie_code, cat.label,
               COUNT(DISTINCT cc.racine_id) AS n_racines_active,
               COALESCE(SUM(
                   CASE WHEN ce.occurred_at >= ? THEN 1 ELSE 0 END
               ), 0) AS n_w_courte,
               COALESCE(SUM(
                   CASE WHEN ce.occurred_at >= ?
                        THEN COALESCE(ce.impact_score, 0)
                        ELSE 0 END
               ), 0.0) AS impact_pondere
        FROM macrs_categories cat
        JOIN causal_cells cc ON cc.categorie_code = cat.categorie_code
        LEFT JOIN causal_events ce ON ce.cell_id = cc.cell_id
        WHERE cc.status = ?
        GROUP BY cat.categorie_code, cat.label, cat.ordre
        ORDER BY impact_pondere DESC, n_w_courte DESC
        """,
        (lower_c, lower_c, STATUS_ACTIVE),
    ).fetchall()

    entries = [
        ParetoCategoryEntry(
            categorie_code=r["categorie_code"],
            label=r["label"],
            n_events_w_courte=int(r["n_w_courte"]),
            impact_pondere=float(r["impact_pondere"]),
            n_racines_active=int(r["n_racines_active"]),
        )
        for r in rows
    ]
    if top_k is not None:
        entries = entries[:top_k]
    return entries


# ---------------------------------------------------------------------
# Drill-down racines au sein d'une catégorie
# ---------------------------------------------------------------------


def pareto_racines_in_category(
    conn: sqlite3.Connection,
    categorie_code: str,
    *,
    now_iso: str,
    top_k: int | None = None,
) -> list[ParetoRacineEntry]:
    """Drill-down : top racines pour une catégorie Δ donnée."""
    lower_c = window_lower_bound(now_iso, W_COURTE_DAYS)
    lower_l = window_lower_bound(now_iso, W_LONGUE_DAYS)
    rows = conn.execute(
        """
        SELECT r.racine_id, r.domaine, r.label, r.predictibilite,
               1 AS n_cells_active,
               COALESCE(SUM(
                   CASE WHEN ce.occurred_at >= ? THEN 1 ELSE 0 END
               ), 0) AS n_w_courte,
               COALESCE(SUM(
                   CASE WHEN ce.occurred_at >= ? THEN 1 ELSE 0 END
               ), 0) AS n_w_longue,
               COALESCE(SUM(
                   CASE WHEN ce.occurred_at >= ?
                        THEN COALESCE(ce.impact_score, 0)
                        ELSE 0 END
               ), 0.0) AS impact_pondere
        FROM macrs_racines r
        JOIN causal_cells cc ON cc.racine_id = r.racine_id
        LEFT JOIN causal_events ce ON ce.cell_id = cc.cell_id
        WHERE cc.categorie_code = ? AND cc.status = ?
        GROUP BY r.racine_id, r.domaine, r.label, r.predictibilite
        ORDER BY impact_pondere DESC, n_w_courte DESC
        """,
        (lower_c, lower_l, lower_c, categorie_code, STATUS_ACTIVE),
    ).fetchall()

    entries: list[ParetoRacineEntry] = []
    for r in rows:
        n_c = int(r["n_w_courte"])
        n_l = int(r["n_w_longue"])
        ratio = (n_c / n_l) if n_l > 0 else None
        entries.append(ParetoRacineEntry(
            racine_id=r["racine_id"],
            domaine=r["domaine"],
            label=r["label"],
            predictibilite=r["predictibilite"],
            n_events_w_courte=n_c,
            n_events_w_longue=n_l,
            impact_pondere=float(r["impact_pondere"]),
            n_cells_active=int(r["n_cells_active"]),
            ratio_emergence=ratio,
        ))
    if top_k is not None:
        entries = entries[:top_k]
    return entries


# ---------------------------------------------------------------------
# Détection racines émergentes / déclinantes
# ---------------------------------------------------------------------


def detect_emerging_racines(
    conn: sqlite3.Connection,
    *,
    now_iso: str,
    min_ratio: float = 1.5,
    min_w_longue: int = 3,
) -> list[EmergingRacine]:
    """Racines avec ratio W_courte/W_longue ≥ `min_ratio`.

    `min_w_longue` filtre les racines sans assez d'historique pour
    que le ratio soit significatif (defauts cadrage : 3 événements
    minimum en W_longue).
    """
    return _detect_with_ratio(
        conn, now_iso=now_iso,
        condition_op=">=",
        threshold=min_ratio,
        min_w_longue=min_w_longue,
        order_desc=True,
    )


def detect_declining_racines(
    conn: sqlite3.Connection,
    *,
    now_iso: str,
    max_ratio: float = 0.5,
    min_w_longue: int = 3,
) -> list[EmergingRacine]:
    """Racines avec ratio W_courte/W_longue ≤ `max_ratio` (en déclin)."""
    return _detect_with_ratio(
        conn, now_iso=now_iso,
        condition_op="<=",
        threshold=max_ratio,
        min_w_longue=min_w_longue,
        order_desc=False,
    )


def _detect_with_ratio(
    conn: sqlite3.Connection,
    *,
    now_iso: str,
    condition_op: str,
    threshold: float,
    min_w_longue: int,
    order_desc: bool,
) -> list[EmergingRacine]:
    lower_c = window_lower_bound(now_iso, W_COURTE_DAYS)
    lower_l = window_lower_bound(now_iso, W_LONGUE_DAYS)
    order = "DESC" if order_desc else "ASC"
    sql = f"""
        SELECT r.racine_id, r.label, r.domaine,
               COALESCE(SUM(
                   CASE WHEN ce.occurred_at >= ? THEN 1 ELSE 0 END
               ), 0) AS n_c,
               COALESCE(SUM(
                   CASE WHEN ce.occurred_at >= ? THEN 1 ELSE 0 END
               ), 0) AS n_l
        FROM macrs_racines r
        JOIN causal_cells cc ON cc.racine_id = r.racine_id
        LEFT JOIN causal_events ce ON ce.cell_id = cc.cell_id
        WHERE cc.status = ?
        GROUP BY r.racine_id, r.label, r.domaine
        HAVING n_l >= ?
           AND (CAST(n_c AS REAL) / n_l) {condition_op} ?
        ORDER BY (CAST(n_c AS REAL) / n_l) {order}
        """
    rows = conn.execute(
        sql, (lower_c, lower_l, STATUS_ACTIVE, min_w_longue, threshold),
    ).fetchall()
    return [
        EmergingRacine(
            racine_id=r["racine_id"],
            label=r["label"],
            domaine=r["domaine"],
            n_w_courte=int(r["n_c"]),
            n_w_longue=int(r["n_l"]),
            ratio_emergence=float(r["n_c"]) / float(r["n_l"]),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------
# Criticité combinée (option D)
# ---------------------------------------------------------------------


def pareto_criticite(
    conn: sqlite3.Connection,
    *,
    now_iso: str,
    top_k: int | None = None,
) -> list[CriticityEntry]:
    """Criticité = fréquence_jour × impact_moyen sur W_courte.

    Fréquence_jour = n_events_w_courte / W_COURTE_DAYS.
    Impact_moyen = Σ impact_score / n (0 si n=0).
    Trié par criticité décroissante.
    """
    lower_c = window_lower_bound(now_iso, W_COURTE_DAYS)
    rows = conn.execute(
        """
        SELECT r.racine_id, r.label,
               COALESCE(SUM(
                   CASE WHEN ce.occurred_at >= ? THEN 1 ELSE 0 END
               ), 0) AS n_c,
               COALESCE(SUM(
                   CASE WHEN ce.occurred_at >= ?
                        THEN COALESCE(ce.impact_score, 0)
                        ELSE 0 END
               ), 0.0) AS sum_impact
        FROM macrs_racines r
        JOIN causal_cells cc ON cc.racine_id = r.racine_id
        LEFT JOIN causal_events ce ON ce.cell_id = cc.cell_id
        WHERE cc.status = ?
        GROUP BY r.racine_id, r.label
        HAVING n_c > 0
        """,
        (lower_c, lower_c, STATUS_ACTIVE),
    ).fetchall()

    entries: list[CriticityEntry] = []
    for r in rows:
        n = int(r["n_c"])
        s = float(r["sum_impact"])
        freq = n / float(W_COURTE_DAYS)
        impact_mean = (s / n) if n > 0 else 0.0
        entries.append(CriticityEntry(
            racine_id=r["racine_id"],
            label=r["label"],
            frequency_per_day=freq,
            impact_mean=impact_mean,
            criticite=freq * impact_mean,
        ))

    entries.sort(key=lambda e: e.criticite, reverse=True)
    if top_k is not None:
        entries = entries[:top_k]
    return entries
