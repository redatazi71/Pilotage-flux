"""MACRS Couche 2 — Matrice opérationnelle dynamique (cellules).

Référence : matrice_operationnelle_specification.md.

À ce stade (A.2) : lifecycle des cellules + 4 statuts. Les fenêtres
glissantes W_courte/W_longue, l'histogramme de délai 8 bins et le
cumul sont ajoutés en A.3 ; les snapshots et le versioning des
poids en A.4.

Statuts :
  - INCOMING   : cellule créée, aucun événement
  - OBSERVING  : 1+ événement, sous-domaine sous le seuil K
  - ACTIVE     : K atteint pour le sous-domaine + 1+ événement

Le seuil **K du sous-domaine** est lu dans `parameters` sous le nom
`macrs_K_<sous_domaine>` (default `K_DEFAULT`). Conséquence du
cadrage §3.3 : toutes les cellules d'un même sous-domaine passent
en ACTIVE simultanément (cohérence Pareto au niveau sous-domaine).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pilotage_flux.cybernetic.macrs.couche1 import (
    RACINES,
    seed_macrs_layer1,
)
from pilotage_flux.parameters import get_num


# Seuil K par défaut (cadrage : K ∈ [20, 50] par sous-domaine,
# estimé à 30-45 jours simulés). On démarre à 30, paramétrable.
K_DEFAULT = 30

# Statuts possibles
STATUS_INCOMING = "INCOMING"
STATUS_OBSERVING = "OBSERVING"
STATUS_ACTIVE = "ACTIVE"
STATUSES = (STATUS_INCOMING, STATUS_OBSERVING, STATUS_ACTIVE)


@dataclass(frozen=True)
class CausalCell:
    cell_id: int
    racine_id: str
    categorie_code: str
    status: str
    n_events_total: int
    first_event_at: str | None
    last_event_at: str | None


def init_cells_from_layer1(conn: sqlite3.Connection) -> int:
    """Matérialise une cellule INCOMING pour chaque couple actif en
    Couche 1.

    Idempotent. Si la Couche 1 n'est pas seedée, l'opération seedée
    automatiquement (les deux couches sont solidaires).

    Renvoie le nombre de cellules créées.
    """
    # Seed solidaire de la Couche 1 si nécessaire
    n_layer1 = conn.execute(
        "SELECT COUNT(*) AS n FROM macrs_incidence"
    ).fetchone()["n"]
    if n_layer1 == 0:
        seed_macrs_layer1(conn)

    created = 0
    rows = conn.execute(
        "SELECT racine_id, categorie_code FROM macrs_incidence "
        "ORDER BY racine_id, categorie_code"
    ).fetchall()
    for r in rows:
        exists = conn.execute(
            "SELECT 1 FROM causal_cells "
            "WHERE racine_id = ? AND categorie_code = ?",
            (r["racine_id"], r["categorie_code"]),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO causal_cells "
            "(racine_id, categorie_code, status) "
            "VALUES (?, ?, ?)",
            (r["racine_id"], r["categorie_code"], STATUS_INCOMING),
        )
        created += 1
    return created


def get_k_for_subdomain(
    conn: sqlite3.Connection, sous_domaine: str,
) -> int:
    """Lit le seuil K du sous-domaine (default K_DEFAULT)."""
    v = get_num(
        conn, scope="global", scope_ref=None,
        name=f"macrs_K_{sous_domaine}", default=float(K_DEFAULT),
    )
    return int(v) if v is not None else K_DEFAULT


def _sous_domaine_of(racine_id: str) -> str:
    """Cherche le sous-domaine d'une racine dans les constantes."""
    for r in RACINES:
        if r.racine_id == racine_id:
            return r.sous_domaine
    raise ValueError(f"racine_id inconnu : {racine_id}")


def record_event(
    conn: sqlite3.Connection,
    racine_id: str,
    categorie_code: str,
    *,
    occurred_at: str,
) -> CausalCell:
    """Enregistre un événement et applique les transitions de statut.

    Pipeline cadrage §7 :
      1. Identifie la cellule (racine, catégorie)
      2. INCOMING → OBSERVING au 1er événement
      3. Met à jour compteurs et timestamps
      4. Vérifie K du sous-domaine : si atteint, bascule **toutes**
         les cellules OBSERVING du sous-domaine en ACTIVE.

    Lève ValueError si la cellule n'existe pas (couple inactif en
    Couche 1).
    """
    row = conn.execute(
        "SELECT cell_id, status FROM causal_cells "
        "WHERE racine_id = ? AND categorie_code = ?",
        (racine_id, categorie_code),
    ).fetchone()
    if row is None:
        raise ValueError(
            f"cellule inactive ou inexistante : ({racine_id}, {categorie_code})"
        )
    cell_id = int(row["cell_id"])

    # Met à jour compteurs + timestamps
    conn.execute(
        """
        UPDATE causal_cells
        SET n_events_total = n_events_total + 1,
            first_event_at = COALESCE(first_event_at, ?),
            last_event_at  = ?,
            status = CASE
                       WHEN status = 'INCOMING' THEN 'OBSERVING'
                       ELSE status
                     END,
            transitioned_observing_at = CASE
                       WHEN status = 'INCOMING' THEN ?
                       ELSE transitioned_observing_at
                     END
        WHERE cell_id = ?
        """,
        (occurred_at, occurred_at, occurred_at, cell_id),
    )

    # Vérifie K du sous-domaine
    sous_domaine = _sous_domaine_of(racine_id)
    _maybe_activate_subdomain(conn, sous_domaine, occurred_at)

    new_row = conn.execute(
        "SELECT cell_id, racine_id, categorie_code, status, "
        "n_events_total, first_event_at, last_event_at "
        "FROM causal_cells WHERE cell_id = ?",
        (cell_id,),
    ).fetchone()
    return CausalCell(
        cell_id=int(new_row["cell_id"]),
        racine_id=new_row["racine_id"],
        categorie_code=new_row["categorie_code"],
        status=new_row["status"],
        n_events_total=int(new_row["n_events_total"]),
        first_event_at=new_row["first_event_at"],
        last_event_at=new_row["last_event_at"],
    )


def _maybe_activate_subdomain(
    conn: sqlite3.Connection, sous_domaine: str, now_iso: str,
) -> int:
    """Si total événements sous-domaine ≥ K, active toutes les
    cellules OBSERVING du sous-domaine.

    Renvoie le nombre de cellules passées en ACTIVE.
    """
    k = get_k_for_subdomain(conn, sous_domaine)
    total = conn.execute(
        """
        SELECT COALESCE(SUM(cc.n_events_total), 0) AS n
        FROM causal_cells cc
        JOIN macrs_racines r ON r.racine_id = cc.racine_id
        WHERE r.sous_domaine = ?
        """,
        (sous_domaine,),
    ).fetchone()
    if total is None or int(total["n"]) < k:
        return 0
    cur = conn.execute(
        """
        UPDATE causal_cells
        SET status = 'ACTIVE',
            transitioned_active_at = ?
        WHERE cell_id IN (
            SELECT cc.cell_id FROM causal_cells cc
            JOIN macrs_racines r ON r.racine_id = cc.racine_id
            WHERE r.sous_domaine = ? AND cc.status = 'OBSERVING'
        )
        """,
        (now_iso, sous_domaine),
    )
    return cur.rowcount


def get_cell(
    conn: sqlite3.Connection,
    racine_id: str,
    categorie_code: str,
) -> CausalCell | None:
    row = conn.execute(
        "SELECT cell_id, racine_id, categorie_code, status, "
        "n_events_total, first_event_at, last_event_at "
        "FROM causal_cells WHERE racine_id = ? AND categorie_code = ?",
        (racine_id, categorie_code),
    ).fetchone()
    if row is None:
        return None
    return CausalCell(
        cell_id=int(row["cell_id"]),
        racine_id=row["racine_id"],
        categorie_code=row["categorie_code"],
        status=row["status"],
        n_events_total=int(row["n_events_total"]),
        first_event_at=row["first_event_at"],
        last_event_at=row["last_event_at"],
    )


def list_cells_by_status(
    conn: sqlite3.Connection, status: str,
) -> list[CausalCell]:
    rows = conn.execute(
        "SELECT cell_id, racine_id, categorie_code, status, "
        "n_events_total, first_event_at, last_event_at "
        "FROM causal_cells WHERE status = ? "
        "ORDER BY racine_id, categorie_code",
        (status,),
    ).fetchall()
    return [
        CausalCell(
            cell_id=int(r["cell_id"]),
            racine_id=r["racine_id"],
            categorie_code=r["categorie_code"],
            status=r["status"],
            n_events_total=int(r["n_events_total"]),
            first_event_at=r["first_event_at"],
            last_event_at=r["last_event_at"],
        )
        for r in rows
    ]


def count_cells_by_status(
    conn: sqlite3.Connection,
) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM causal_cells GROUP BY status"
    ).fetchall()
    return {r["status"]: int(r["n"]) for r in rows}
