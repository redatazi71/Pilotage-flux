"""V13.F — Lot-sizing takt-minimal.

Objectif QCDS : **minimiser le nombre d'OFs** (chaque OF ajoute des
coûts fixes MOI + setup + coordination). Par défaut 1 SO → 1 OF.

Split **uniquement** si la charge d'un candidate sur le WS goulot
dépasse le budget quotidien à target_saturation → n_splits = ceil(
charge_goulot / budget_goulot_par_jour). Le split est alors réparti
uniformément entre les sous-candidates.

Différence avec les stratégies classiques :
- **LFL** (Lot for Lot) — 1 SO = 1 OF, quelle que soit la faisabilité.
  Peut créer un OF ingérable si le goulot ne peut l'absorber.
- **EOQ** — lot optimal cost setup/carrying, ignore le takt.
- **POQ** — regroupe demandes sur période fixe, ignore le goulot.
- **V13.F takt-minimal** — LFL par défaut, split UNIQUEMENT si la
  contrainte goulot l'exige (compromis QCDS optimal).

Le flag `smoothing_lot_sizing` contrôle :
- `off` (default) : LFL pur, 1 candidate → 1 candidate (pas de split)
- `takt_minimal` : split seulement si nécessaire (QCDS-optimal)
"""

from __future__ import annotations

import math
import sqlite3
import uuid

from pilotage_flux.parameters import get_num, workstation_capacity_factor


def _get_lot_sizing_mode(conn: sqlite3.Connection) -> str:
    """V13.F — Lit `smoothing_lot_sizing` (default 0 = off).

    Renvoie "off" ou "takt_minimal".
    """
    val = get_num(
        conn, scope="global", scope_ref=None,
        name="smoothing_lot_sizing", default=0.0,
    )
    if val is None or float(val) <= 0.5:
        return "off"
    return "takt_minimal"


def _daily_minutes(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT daily_minutes FROM calendars LIMIT 1"
    ).fetchone()
    if row and row["daily_minutes"]:
        return int(row["daily_minutes"])
    return 480


def _unit_time_on_ws(
    conn: sqlite3.Connection, article_id: str, ws_id: str,
) -> float:
    """Somme des unit_time des ops de cet article sur ce WS (typiquement 1 op)."""
    row = conn.execute(
        """SELECT COALESCE(SUM(unit_time_min), 0.0) AS ut
           FROM routing_operations
           WHERE article_id = ? AND workstation_id = ?""",
        (article_id, ws_id),
    ).fetchone()
    return float(row["ut"]) if row else 0.0


def compute_min_splits(
    conn: sqlite3.Connection,
    article_id: str,
    quantity: float,
    bottleneck_ws: str | None,
    target_saturation: float = 0.85,
    tolerance_ratio: float = 2.0,
) -> int:
    """V13.F — Nombre minimum de splits pour respecter le débit goulot.

    **Objectif QCDS** : minimiser le nombre d'OFs. Split UNIQUEMENT quand
    la charge dépasse `tolerance_ratio × budget_par_jour` (default 2×,
    soit ~170 % de saturation ponctuelle). Cela laisse V13.D/E gérer les
    petits dépassements par dégradation gracieuse (placement au jour
    minimisant le surplus) sans créer d'OF supplémentaire.

    Le split n'est justifié que si le lot est physiquement inplaçable
    en 1 slot (charge > 2 × budget quotidien → au moins 1 jour perdu).

    Renvoie n = max(1, ceil(charge_goulot / (tolerance_ratio × budget)))
    """
    if bottleneck_ws is None or quantity <= 0:
        return 1
    unit_time = _unit_time_on_ws(conn, article_id, bottleneck_ws)
    if unit_time <= 0:
        return 1
    charge_goulot = quantity * unit_time
    daily_min = _daily_minutes(conn)
    capa = workstation_capacity_factor(conn, bottleneck_ws)
    budget_par_jour = daily_min * capa * target_saturation
    if budget_par_jour <= 0:
        return 1
    threshold = tolerance_ratio * budget_par_jour
    if charge_goulot <= threshold:
        return 1  # V13.D/E gère par dégradation gracieuse
    return max(1, math.ceil(charge_goulot / budget_par_jour))


def split_candidates_for_takt(
    conn: sqlite3.Connection,
    candidates: list,
    bottleneck_ws: str | None,
    target_saturation: float = 0.85,
) -> list:
    """V13.F — Split minimal des candidates pour respecter le takt goulot.

    Pour chaque candidate :
    - Calcule n_splits via `compute_min_splits`
    - Si n_splits == 1 : conserve tel quel (LFL, aucun OF ajouté)
    - Si n_splits > 1 :
        - Insère (n_splits − 1) nouvelles candidates dans candidate_orders
          avec ids uniques ; conserve la 1re candidate mais divise sa qty
        - Renvoie la liste augmentée de sub-candidates

    Ne persiste PAS de flag "split" sur les candidates originaux ; les
    nouvelles candidates sont identifiables par leur pattern d'id
    (`<orig>_split_<i>`).

    Renvoie la nouvelle liste de candidates (ordre préservé, sub-candidates
    accolés après leur parent).
    """
    if not candidates:
        return []
    out: list = []
    for cand in candidates:
        qty = float(cand["qty_in_contract"])
        article_id = cand["article_id"]
        cid = cand["candidate_id"]
        n = compute_min_splits(
            conn, article_id, qty, bottleneck_ws, target_saturation,
        )
        if n <= 1:
            out.append(cand)
            continue
        # Split en n morceaux : la 1re candidate garde son id, les
        # (n-1) suivantes sont nouvelles.
        qty_per_split = qty / n
        # 1er morceau : update la candidate existante (qty réduite)
        conn.execute(
            "UPDATE candidate_orders SET quantity = ? WHERE candidate_id = ?",
            (qty_per_split, cid),
        )
        # Prépare le 1er morceau retourné avec la nouvelle qty
        first_cand = dict(cand) if not isinstance(cand, dict) else dict(cand)
        first_cand["qty_in_contract"] = qty_per_split
        first_cand["quantity"] = qty_per_split
        out.append(first_cand)
        # Récupère info source pour dupliquer (so_id, zone, dates)
        row = conn.execute(
            "SELECT sales_order_id, article_id, earliest_start, latest_end, "
            "status, zone FROM candidate_orders WHERE candidate_id = ?",
            (cid,),
        ).fetchone()
        for i in range(1, n):
            new_cid = f"{cid}_split_{i}"
            conn.execute(
                """INSERT INTO candidate_orders
                (candidate_id, sales_order_id, article_id, quantity,
                 earliest_start, latest_end, status, zone)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (new_cid, row["sales_order_id"], row["article_id"],
                 qty_per_split, row["earliest_start"],
                 row["latest_end"], row["status"], row["zone"]),
            )
            sub_cand = {
                "candidate_id": new_cid,
                "sales_order_id": row["sales_order_id"],
                "article_id": row["article_id"],
                "quantity": qty_per_split,
                "qty_in_contract": qty_per_split,
                "status": row["status"],
                "zone": row["zone"],
            }
            out.append(sub_cand)
    return out
