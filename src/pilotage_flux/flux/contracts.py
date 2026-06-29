"""Création, versionnement et requêtage des contrats de flux.

Un contrat de flux regroupe plusieurs candidates négociés sur un même
horizon. Chaque modification (ajout/retrait d'un candidate) crée une
nouvelle version qui devient `current_version`. Les anciennes versions
restent consultables.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class FluxContract:
    contract_id: str
    horizon_label: str
    horizon_start: str
    horizon_end: str
    status: str
    current_version: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class FluxContractVersion:
    contract_id: str
    version: int
    takt_target_min: float | None
    wip_target: float | None
    total_quantity: float
    is_coherent: bool
    notes: str | None
    created_at: str


def _next_contract_id(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT contract_id FROM flux_contracts ORDER BY contract_id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return "FX-0001"
    last = row["contract_id"]
    try:
        n = int(last.split("-")[-1])
    except (ValueError, IndexError):
        n = 0
    return f"FX-{n + 1:04d}"


def _row_to_contract(row: sqlite3.Row) -> FluxContract:
    return FluxContract(
        contract_id=row["contract_id"],
        horizon_label=row["horizon_label"],
        horizon_start=row["horizon_start"],
        horizon_end=row["horizon_end"],
        status=row["status"],
        current_version=int(row["current_version"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_version(row: sqlite3.Row) -> FluxContractVersion:
    return FluxContractVersion(
        contract_id=row["contract_id"],
        version=int(row["version"]),
        takt_target_min=(
            float(row["takt_target_min"]) if row["takt_target_min"] is not None else None
        ),
        wip_target=(
            float(row["wip_target"]) if row["wip_target"] is not None else None
        ),
        total_quantity=float(row["total_quantity"]),
        is_coherent=bool(row["is_coherent"]),
        notes=row["notes"],
        created_at=row["created_at"],
    )


def _candidates_negociable_quantity(
    conn: sqlite3.Connection, candidate_ids: list[str]
) -> dict[str, float]:
    """Vérifie que les candidates sont en zone négociable et renvoie leurs qtés."""
    if not candidate_ids:
        return {}
    placeholders = ",".join("?" * len(candidate_ids))
    rows = conn.execute(
        f"""
        SELECT candidate_id, quantity, zone
        FROM candidate_orders
        WHERE candidate_id IN ({placeholders})
        """,
        candidate_ids,
    ).fetchall()
    found = {r["candidate_id"]: r for r in rows}
    missing = set(candidate_ids) - set(found)
    if missing:
        raise ValueError(f"Candidate(s) inconnu(s) : {sorted(missing)}")
    bad_zone = [
        r["candidate_id"] for r in found.values() if r["zone"] != "negociable"
    ]
    if bad_zone:
        raise ValueError(
            f"Candidate(s) doivent être en zone 'negociable' : {bad_zone}"
        )
    return {cid: float(found[cid]["quantity"]) for cid in candidate_ids}


def _already_in_active_contract(
    conn: sqlite3.Connection, candidate_ids: list[str]
) -> list[str]:
    """Retourne les candidates déjà liés à un contrat actif (non archivé)."""
    if not candidate_ids:
        return []
    placeholders = ",".join("?" * len(candidate_ids))
    rows = conn.execute(
        f"""
        SELECT DISTINCT l.candidate_id
        FROM flux_contract_links l
        JOIN flux_contracts c ON c.contract_id = l.contract_id
                            AND c.current_version = l.version
        WHERE l.candidate_id IN ({placeholders})
          AND c.status NOT IN ('archived')
        """,
        candidate_ids,
    ).fetchall()
    return [r["candidate_id"] for r in rows]


def _compute_takt_target(
    horizon_start: str, horizon_end: str, total_qty: float
) -> float | None:
    """Calcule un takt cible naïf : (minutes ouvrées sur l'horizon) / total_qty.

    Heuristique V1 : on suppose 8h/j sur jours ouvrés (lun-ven). Le takt
    réel dépendra de la capacité goulot (calculé en cohérence).
    """
    from datetime import date

    if total_qty <= 0:
        return None
    try:
        d_start = date.fromisoformat(horizon_start)
        d_end = date.fromisoformat(horizon_end)
    except ValueError:
        return None
    delta_days = max((d_end - d_start).days + 1, 1)
    # Approxim : on compte tous les jours x 480 min (le calendrier exact sera
    # appliqué lors du check de cohérence).
    horizon_minutes = delta_days * 480
    return horizon_minutes / total_qty


def create_contract(
    conn: sqlite3.Connection,
    *,
    horizon_label: str,
    horizon_start: str,
    horizon_end: str,
    candidate_ids: list[str],
    wip_target: float | None = None,
    notes: str | None = None,
) -> FluxContract:
    """Crée un contrat de flux v1 regroupant les candidates donnés.

    Tous les candidates doivent être en zone 'négociable' et ne pas être
    déjà liés à un autre contrat actif.
    """
    if not candidate_ids:
        raise ValueError("Un contrat doit contenir au moins un candidate")

    quantities = _candidates_negociable_quantity(conn, candidate_ids)
    already = _already_in_active_contract(conn, candidate_ids)
    if already:
        raise ValueError(
            f"Candidate(s) déjà dans un contrat actif : {already}"
        )

    contract_id = _next_contract_id(conn)
    total_qty = sum(quantities.values())
    takt = _compute_takt_target(horizon_start, horizon_end, total_qty)
    # WIP target par défaut : moyenne des qtés candidates (= 1 OF en cours)
    if wip_target is None and quantities:
        wip_target = total_qty / max(len(quantities), 1)

    conn.execute(
        """
        INSERT INTO flux_contracts
            (contract_id, horizon_label, horizon_start, horizon_end, status, current_version)
        VALUES (?, ?, ?, ?, 'draft', 1)
        """,
        (contract_id, horizon_label, horizon_start, horizon_end),
    )
    conn.execute(
        """
        INSERT INTO flux_contract_versions
            (contract_id, version, takt_target_min, wip_target, total_quantity, notes)
        VALUES (?, 1, ?, ?, ?, ?)
        """,
        (contract_id, takt, wip_target, total_qty, notes),
    )
    for idx, cid in enumerate(candidate_ids):
        conn.execute(
            """
            INSERT INTO flux_contract_links
                (contract_id, version, candidate_id, qty_in_contract, sequence_idx)
            VALUES (?, 1, ?, ?, ?)
            """,
            (contract_id, cid, quantities[cid], idx),
        )

    return _row_to_contract(
        conn.execute(
            "SELECT * FROM flux_contracts WHERE contract_id = ?", (contract_id,)
        ).fetchone()
    )


def _copy_to_new_version(
    conn: sqlite3.Connection,
    contract_id: str,
    *,
    new_candidate_ids: list[str],
    new_quantities: dict[str, float],
    notes: str | None,
) -> int:
    """Crée une nouvelle version v+1 avec la liste candidate donnée."""
    contract = fetch_contract(conn, contract_id)
    if contract is None:
        raise ValueError(f"Contrat inconnu : {contract_id}")
    new_version = contract.current_version + 1
    total_qty = sum(new_quantities.values())
    takt = _compute_takt_target(
        contract.horizon_start, contract.horizon_end, total_qty
    )
    # WIP target conservée si elle existait, sinon recalc
    prev_version = fetch_version(conn, contract_id, contract.current_version)
    wip_target = prev_version.wip_target if prev_version else None
    if wip_target is None and new_quantities:
        wip_target = total_qty / max(len(new_quantities), 1)

    conn.execute(
        """
        INSERT INTO flux_contract_versions
            (contract_id, version, takt_target_min, wip_target, total_quantity, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (contract_id, new_version, takt, wip_target, total_qty, notes),
    )
    for idx, cid in enumerate(new_candidate_ids):
        conn.execute(
            """
            INSERT INTO flux_contract_links
                (contract_id, version, candidate_id, qty_in_contract, sequence_idx)
            VALUES (?, ?, ?, ?, ?)
            """,
            (contract_id, new_version, cid, new_quantities[cid], idx),
        )
    conn.execute(
        """
        UPDATE flux_contracts
        SET current_version = ?, updated_at = datetime('now'), status = 'draft'
        WHERE contract_id = ?
        """,
        (new_version, contract_id),
    )
    return new_version


def add_candidate_to_contract(
    conn: sqlite3.Connection,
    contract_id: str,
    candidate_id: str,
    *,
    notes: str | None = None,
) -> int:
    """Ajoute un candidate au contrat → nouvelle version. Renvoie la version créée."""
    contract = fetch_contract(conn, contract_id)
    if contract is None:
        raise ValueError(f"Contrat inconnu : {contract_id}")
    if contract.status == "frozen":
        raise ValueError(
            f"Contrat {contract_id} gelé : modification directe interdite "
            f"(passer par P3 inverse en L1.6)"
        )
    current_cands = get_candidates_in_version(conn, contract_id, contract.current_version)
    current_ids = [c["candidate_id"] for c in current_cands]
    if candidate_id in current_ids:
        raise ValueError(f"Candidate {candidate_id} déjà dans le contrat")

    quantities = _candidates_negociable_quantity(conn, [candidate_id])
    already = _already_in_active_contract(conn, [candidate_id])
    if already:
        raise ValueError(
            f"Candidate {candidate_id} déjà dans un autre contrat actif"
        )

    new_ids = current_ids + [candidate_id]
    new_quantities = {
        c["candidate_id"]: float(c["qty_in_contract"]) for c in current_cands
    }
    new_quantities[candidate_id] = quantities[candidate_id]

    return _copy_to_new_version(
        conn, contract_id,
        new_candidate_ids=new_ids,
        new_quantities=new_quantities,
        notes=notes or f"Ajout de {candidate_id}",
    )


def remove_candidate_from_contract(
    conn: sqlite3.Connection,
    contract_id: str,
    candidate_id: str,
    *,
    notes: str | None = None,
) -> int:
    """Retire un candidate du contrat → nouvelle version."""
    contract = fetch_contract(conn, contract_id)
    if contract is None:
        raise ValueError(f"Contrat inconnu : {contract_id}")
    if contract.status == "frozen":
        raise ValueError(
            f"Contrat {contract_id} gelé : modification directe interdite "
            f"(passer par P3 inverse en L1.6)"
        )
    current_cands = get_candidates_in_version(conn, contract_id, contract.current_version)
    current_ids = [c["candidate_id"] for c in current_cands]
    if candidate_id not in current_ids:
        raise ValueError(f"Candidate {candidate_id} absent du contrat")
    if len(current_ids) == 1:
        raise ValueError("Impossible de retirer le dernier candidate (contrat vide)")

    new_ids = [cid for cid in current_ids if cid != candidate_id]
    new_quantities = {
        c["candidate_id"]: float(c["qty_in_contract"])
        for c in current_cands
        if c["candidate_id"] != candidate_id
    }

    return _copy_to_new_version(
        conn, contract_id,
        new_candidate_ids=new_ids,
        new_quantities=new_quantities,
        notes=notes or f"Retrait de {candidate_id}",
    )


def fetch_contract(conn: sqlite3.Connection, contract_id: str) -> FluxContract | None:
    row = conn.execute(
        "SELECT * FROM flux_contracts WHERE contract_id = ?", (contract_id,)
    ).fetchone()
    return _row_to_contract(row) if row else None


def fetch_version(
    conn: sqlite3.Connection, contract_id: str, version: int
) -> FluxContractVersion | None:
    row = conn.execute(
        """
        SELECT * FROM flux_contract_versions
        WHERE contract_id = ? AND version = ?
        """,
        (contract_id, version),
    ).fetchone()
    return _row_to_version(row) if row else None


def get_candidates_in_version(
    conn: sqlite3.Connection, contract_id: str, version: int
) -> list[dict]:
    """Liste les candidates d'une version donnée du contrat."""
    rows = conn.execute(
        """
        SELECT l.candidate_id, l.qty_in_contract, l.sequence_idx,
               co.article_id, co.zone
        FROM flux_contract_links l
        JOIN candidate_orders co ON co.candidate_id = l.candidate_id
        WHERE l.contract_id = ? AND l.version = ?
        ORDER BY l.sequence_idx ASC
        """,
        (contract_id, version),
    ).fetchall()
    return [dict(r) for r in rows]


def list_contracts(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
) -> list[FluxContract]:
    sql = "SELECT * FROM flux_contracts WHERE 1=1"
    params: list[str] = []
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC, contract_id ASC"
    return [_row_to_contract(r) for r in conn.execute(sql, params)]
