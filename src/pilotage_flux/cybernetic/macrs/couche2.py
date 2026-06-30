"""MACRS Couche 2 — Matrice opérationnelle dynamique (cellules).

Référence : matrice_operationnelle_specification.md.

A.2 : lifecycle 4 statuts ; A.3 : files événementielles + fenêtres
glissantes W_courte=30j et W_longue=90j + histogramme 8 bins +
cumul total.

Statuts :
  - INCOMING   : cellule créée, aucun événement
  - OBSERVING  : 1+ événement, sous-domaine sous le seuil K
  - ACTIVE     : K atteint pour le sous-domaine + 1+ événement

Le seuil **K du sous-domaine** est lu dans `parameters` sous le nom
`macrs_K_<sous_domaine>` (default `K_DEFAULT`). Conséquence du
cadrage §3.3 : toutes les cellules d'un même sous-domaine passent
en ACTIVE simultanément (cohérence Pareto au niveau sous-domaine).

Temporalités (§2.2) :
  - W_courte = 30 jours simulés glissants → Pareto courant
  - W_longue = 90 jours simulés glissants → référence stable
  - Cumul total → mémoire archivée, jamais effacée

Bins délai (§2.4, 8 niveaux, plage en heures) :
  b0_1h [0,1), b1_4h [1,4), b4_24h [4,24),
  b1_3j [24,72), b3_7j [72,168), b7_14j [168,336),
  b14_30j [336,720), b30_90j [720,2160) puis cap.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from pilotage_flux.cybernetic.macrs.couche1 import (
    RACINES,
    seed_macrs_layer1,
)
from pilotage_flux.parameters import get_num


# -------- Fenêtres temporelles --------
W_COURTE_DAYS = 30   # spec §2.2
W_LONGUE_DAYS = 90   # spec §2.2


# -------- Bins de délai (heures) --------
# Ordre canonique du document, exclusif borne haute.
BINS: tuple[tuple[str, float, float], ...] = (
    ("b0_1h",    0.0,     1.0),
    ("b1_4h",    1.0,     4.0),
    ("b4_24h",   4.0,    24.0),
    ("b1_3j",   24.0,    72.0),
    ("b3_7j",   72.0,   168.0),
    ("b7_14j", 168.0,   336.0),
    ("b14_30j",336.0,   720.0),
    ("b30_90j",720.0,  2160.0),
)
BIN_LABELS = tuple(b[0] for b in BINS)


def delay_to_bin(delay_hours: float) -> str:
    """Mappe un délai (heures, peut être négatif → b0_1h) vers un bin.

    Au-delà de 2160 h (90 j), on cape sur b30_90j (cf. spec §2.4).
    Négatif ou nul → b0_1h.
    """
    if delay_hours <= 0:
        return "b0_1h"
    for label, low, high in BINS:
        if low <= delay_hours < high:
            return label
    return "b30_90j"   # overflow → cap


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
    delay_hours: float | None = None,
    impact_score: float | None = None,
) -> CausalCell:
    """Enregistre un événement et applique les transitions de statut.

    Pipeline cadrage §7 (mise à jour synchrone, §6) :
      1. Identifie la cellule (racine, catégorie)
      2. INCOMING → OBSERVING au 1er événement
      3. Insère une ligne dans `causal_events` (file événementielle)
      4. Met à jour compteurs, timestamps et bin du cumul histogramme
      5. Vérifie K du sous-domaine : si atteint, bascule **toutes**
         les cellules OBSERVING du sous-domaine en ACTIVE.

    Paramètres :
      delay_hours  : délai entre racine et manifestation (heures).
                     Si None, pas d'incrément histogramme.
      impact_score : score pondéré optionnel.

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

    bin_label = delay_to_bin(delay_hours) if delay_hours is not None else None

    # Atomique : ligne événement + compteurs + bin cumul + statut
    conn.execute(
        "INSERT INTO causal_events "
        "(cell_id, occurred_at, delay_bin, delay_hours, impact_score) "
        "VALUES (?, ?, ?, ?, ?)",
        (cell_id, occurred_at, bin_label, delay_hours, impact_score),
    )

    # Update compteurs + timestamps + transition INCOMING → OBSERVING
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

    # Incrément du bin cumul (column name dynamique mais bornée à 8
    # valeurs connues → safe).
    if bin_label is not None:
        col = f"bin_cumul_{bin_label}"
        conn.execute(
            f"UPDATE causal_cells SET {col} = {col} + 1 WHERE cell_id = ?",
            (cell_id,),
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


# ---------------------------------------------------------------------
# A.3 — Agrégats temporels (fenêtres glissantes + histogramme)
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class CellAggregates:
    """Agrégats d'une cellule à un instant `now`.

    Les comptes courte/longue sont calculés à la demande depuis
    `causal_events` (filtre `occurred_at >= now - W`). Le cumul
    est lu directement sur `causal_cells`.
    """
    cell_id: int
    racine_id: str
    categorie_code: str
    status: str
    n_w_courte: int
    n_w_longue: int
    n_cumul: int
    histogram_w_courte: dict[str, int]
    histogram_w_longue: dict[str, int]
    histogram_cumul: dict[str, int]

    @property
    def ratio_emergence(self) -> float | None:
        """W_courte / W_longue (cf. spec §2.5).

        > 1 : racine émergente
        < 1 : racine s'éteignant
        ≈ 1 : régime stable
        None : W_longue = 0 (insuffisant)
        """
        if self.n_w_longue == 0:
            return None
        return self.n_w_courte / self.n_w_longue


def _empty_histogram() -> dict[str, int]:
    return {label: 0 for label in BIN_LABELS}


def _window_lower_bound(now_iso: str, window_days: int) -> str:
    """Calcule la borne inférieure de la fenêtre (now - window_days)."""
    now_dt = datetime.fromisoformat(now_iso)
    lower = now_dt - timedelta(days=window_days)
    return lower.isoformat()


def aggregate_cell(
    conn: sqlite3.Connection,
    cell_id: int,
    *,
    now_iso: str,
) -> CellAggregates:
    """Calcule les agrégats W_courte (30j), W_longue (90j) et cumul
    pour une cellule.

    Coût : O(log N) (filtre indexé sur cell_id + occurred_at).
    """
    base_row = conn.execute(
        "SELECT cell_id, racine_id, categorie_code, status, "
        "n_events_total, "
        "bin_cumul_b0_1h, bin_cumul_b1_4h, bin_cumul_b4_24h, "
        "bin_cumul_b1_3j, bin_cumul_b3_7j, bin_cumul_b7_14j, "
        "bin_cumul_b14_30j, bin_cumul_b30_90j "
        "FROM causal_cells WHERE cell_id = ?",
        (cell_id,),
    ).fetchone()
    if base_row is None:
        raise ValueError(f"cell_id {cell_id} introuvable")

    hist_cumul = {label: int(base_row[f"bin_cumul_{label}"])
                  for label in BIN_LABELS}

    lower_c = _window_lower_bound(now_iso, W_COURTE_DAYS)
    lower_l = _window_lower_bound(now_iso, W_LONGUE_DAYS)

    rows = conn.execute(
        "SELECT delay_bin, occurred_at FROM causal_events "
        "WHERE cell_id = ? AND occurred_at >= ?",
        (cell_id, lower_l),
    ).fetchall()

    hist_c = _empty_histogram()
    hist_l = _empty_histogram()
    n_c = 0
    n_l = 0
    for r in rows:
        # W_longue garantie par WHERE
        n_l += 1
        if r["delay_bin"] in hist_l:
            hist_l[r["delay_bin"]] += 1
        # W_courte = sous-ensemble
        if r["occurred_at"] >= lower_c:
            n_c += 1
            if r["delay_bin"] in hist_c:
                hist_c[r["delay_bin"]] += 1

    return CellAggregates(
        cell_id=cell_id,
        racine_id=base_row["racine_id"],
        categorie_code=base_row["categorie_code"],
        status=base_row["status"],
        n_w_courte=n_c,
        n_w_longue=n_l,
        n_cumul=int(base_row["n_events_total"]),
        histogram_w_courte=hist_c,
        histogram_w_longue=hist_l,
        histogram_cumul=hist_cumul,
    )


def aggregate_cell_by_couple(
    conn: sqlite3.Connection,
    racine_id: str,
    categorie_code: str,
    *,
    now_iso: str,
) -> CellAggregates:
    """Variante par couple (racine, catégorie)."""
    row = conn.execute(
        "SELECT cell_id FROM causal_cells "
        "WHERE racine_id = ? AND categorie_code = ?",
        (racine_id, categorie_code),
    ).fetchone()
    if row is None:
        raise ValueError(
            f"cellule inactive : ({racine_id}, {categorie_code})"
        )
    return aggregate_cell(conn, int(row["cell_id"]), now_iso=now_iso)


def list_events_in_window(
    conn: sqlite3.Connection,
    cell_id: int,
    *,
    now_iso: str,
    window_days: int,
) -> list[dict]:
    """Renvoie les événements d'une cellule sur une fenêtre arbitraire.

    Utile pour debug / audit (spec §4.3 : événements expirés
    consultables).
    """
    lower = _window_lower_bound(now_iso, window_days)
    rows = conn.execute(
        "SELECT cell_event_id, occurred_at, delay_bin, delay_hours, "
        "impact_score FROM causal_events "
        "WHERE cell_id = ? AND occurred_at >= ? ORDER BY occurred_at",
        (cell_id, lower),
    ).fetchall()
    return [dict(r) for r in rows]
