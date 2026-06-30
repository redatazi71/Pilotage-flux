"""Moteur Delta unifié — niveaux d'action (B.1).

Arbitrage doctrinal :
  - **Cadrage v1.3 §3.11** : 4 niveaux (N1..N4)
        N1 absorption (CPM, marges)        → auto
        N2 ajustement auto                  → auto, scope local
        N3 replan locale                    → validation humaine
        N4 replan complète                  → validation humaine

  - **CDC v1 §11** : 6 niveaux d'action explicites
        L1 informer
        L2 surveiller
        L3 corriger_local
        L4 replanifier_local
        L5 escalader
        L6 replanifier_global

On retient le vocabulaire CDC (6 niveaux) comme grammaire d'action et
on map chaque niveau sur l'un des 4 niveaux du cadrage v1.3. Le flag
`requires_human` exprime la subsidiarité humaine (cadrage : N3/N4).

Cohérence préservée : un Pareto agrégé par cadrage_level donne 4
niveaux conformes ; un Pareto par niveau_code donne la granularité
fine d'action.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


# Codes (ordre d'escalade croissant)
L_INFORMER = "L1"
L_SURVEILLER = "L2"
L_CORRIGER_LOCAL = "L3"
L_REPLANIFIER_LOCAL = "L4"
L_ESCALADER = "L5"
L_REPLANIFIER_GLOBAL = "L6"

NIVEAUX_ORDRE = (
    L_INFORMER, L_SURVEILLER, L_CORRIGER_LOCAL,
    L_REPLANIFIER_LOCAL, L_ESCALADER, L_REPLANIFIER_GLOBAL,
)


# Catalogue canonique des 6 niveaux (ordre, mapping, scope,
# subsidiarité).
NIVEAUX_CANONIQUES: tuple[tuple, ...] = (
    # (code, label, cadrage_level, requires_human, scope, description,
    #  ordre)
    ("L1", "informer", 1, 0, "none",
     "Observation seule, écart consigné, aucune action.", 1),
    ("L2", "surveiller", 1, 0, "none",
     "Surveillance accrue de l'objet concerné, fréquence "
     "augmentée, aucune action corrective.", 2),
    ("L3", "corriger_local", 2, 0, "local",
     "Ajustement automatique local (replan d'op, réaffectation "
     "ressource, marge CPM consommée). Pas de validation humaine.", 3),
    ("L4", "replanifier_local", 3, 1, "local",
     "Replan local du périmètre concerné, soumis à validation "
     "humaine via approval_queue (cadrage N3).", 4),
    ("L5", "escalader", 3, 1, "local",
     "Escalade hiérarchique sans replan immédiat : signal de "
     "transition vers replan global si cas non résolu.", 5),
    ("L6", "replanifier_global", 4, 1, "global",
     "Replan complet du périmètre couvert par la zone négociable, "
     "soumis à validation humaine (cadrage N4).", 6),
)


@dataclass(frozen=True)
class DeltaActionLevel:
    niveau_code: str
    label: str
    cadrage_level: int
    requires_human: bool
    scope: str
    description: str
    ordre: int


def seed_default_delta_levels(conn: sqlite3.Connection) -> int:
    """Seed idempotent des 6 niveaux canoniques.

    Renvoie le nombre de niveaux insérés.
    """
    inserted = 0
    for (code, label, cadrage_level, requires_human, scope,
         description, ordre) in NIVEAUX_CANONIQUES:
        exists = conn.execute(
            "SELECT 1 FROM delta_action_levels WHERE niveau_code = ?",
            (code,),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO delta_action_levels "
            "(niveau_code, label, cadrage_level, requires_human, "
            " scope, description, ordre) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (code, label, cadrage_level, requires_human, scope,
             description, ordre),
        )
        inserted += 1
    return inserted


def list_delta_levels(
    conn: sqlite3.Connection,
) -> list[DeltaActionLevel]:
    """Renvoie les 6 niveaux dans l'ordre d'escalade."""
    rows = conn.execute(
        "SELECT niveau_code, label, cadrage_level, requires_human, "
        "scope, description, ordre "
        "FROM delta_action_levels ORDER BY ordre"
    ).fetchall()
    return [_row_to_level(r) for r in rows]


def get_delta_level(
    conn: sqlite3.Connection, niveau_code: str,
) -> DeltaActionLevel | None:
    row = conn.execute(
        "SELECT niveau_code, label, cadrage_level, requires_human, "
        "scope, description, ordre "
        "FROM delta_action_levels WHERE niveau_code = ?",
        (niveau_code,),
    ).fetchone()
    return _row_to_level(row) if row else None


def list_levels_for_cadrage(
    conn: sqlite3.Connection, cadrage_level: int,
) -> list[DeltaActionLevel]:
    """Renvoie les niveaux CDC mappés sur un niveau cadrage donné
    (utile pour requêtes doctrinales agrégées N1..N4)."""
    rows = conn.execute(
        "SELECT niveau_code, label, cadrage_level, requires_human, "
        "scope, description, ordre "
        "FROM delta_action_levels WHERE cadrage_level = ? "
        "ORDER BY ordre",
        (cadrage_level,),
    ).fetchall()
    return [_row_to_level(r) for r in rows]


def _row_to_level(row: sqlite3.Row) -> DeltaActionLevel:
    return DeltaActionLevel(
        niveau_code=row["niveau_code"],
        label=row["label"],
        cadrage_level=int(row["cadrage_level"]),
        requires_human=bool(row["requires_human"]),
        scope=row["scope"],
        description=row["description"],
        ordre=int(row["ordre"]),
    )
