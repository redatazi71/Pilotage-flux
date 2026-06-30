"""V12.4 — Rôles et permissions pour validation L3/L4.

Trois rôles hiérarchiques :

  - **operator**   : peut approuver L3 (replan local)
  - **supervisor** : peut approuver L3 et L4 (replan global)
  - **admin**      : permissions complètes + gestion des rôles

L'identité (`user_id`) est libre — typiquement l'email ou le login.
Le mapping user_id → rôle est persisté dans la table `user_roles`.
"""

from __future__ import annotations

import sqlite3
from typing import Final

from pilotage_flux.cybernetic.delta_engine.autonomy_levels import (
    AUTONOMY_LEVEL_L3,
    AUTONOMY_LEVEL_L4,
)


ROLE_OPERATOR: Final[str] = "operator"
ROLE_SUPERVISOR: Final[str] = "supervisor"
ROLE_ADMIN: Final[str] = "admin"

ROLES: Final[tuple[str, ...]] = (ROLE_OPERATOR, ROLE_SUPERVISOR, ROLE_ADMIN)


_PERMISSION_MATRIX = {
    AUTONOMY_LEVEL_L3: {ROLE_OPERATOR, ROLE_SUPERVISOR, ROLE_ADMIN},
    AUTONOMY_LEVEL_L4: {ROLE_SUPERVISOR, ROLE_ADMIN},
}


def can_approve(role: str, autonomy_level: str) -> bool:
    """Renvoie True si le rôle peut approuver une décision du niveau donné."""
    return role in _PERMISSION_MATRIX.get(autonomy_level, set())


def set_user_role(
    conn: sqlite3.Connection, user_id: str, role: str,
) -> None:
    """Assigne (ou met à jour) un rôle pour un utilisateur."""
    if role not in ROLES:
        raise ValueError(f"Rôle inconnu : {role!r} (attendu : {ROLES})")
    conn.execute(
        """
        INSERT INTO user_roles (user_id, role)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            role = excluded.role,
            modified_at = datetime('now')
        """,
        (user_id, role),
    )


def get_user_role(
    conn: sqlite3.Connection, user_id: str,
) -> str | None:
    """Renvoie le rôle de l'utilisateur, ou None si non enregistré."""
    row = conn.execute(
        "SELECT role FROM user_roles WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    return row["role"] if row else None
