"""Distribution de contrats en sortie P3 (Goldilocks composant #5).

Cadrage v1.3 §3.10.4 : quand P3 fige une tranche (`freeze_batches`),
les engagements collectifs des contrats de flux doivent être
**distribués au grain opération** sous forme de Contrats de Production
PC=(T,Ep,Er,C,O) — composant #4.

Parcours :

    freeze_batch
        → freeze_batch_contracts (contract_id, version)
            → flux_contract_links (candidate_id)
                → manufacturing_orders (of_id where candidate_id = ?)
                    → order_operations (of_op_id)
                        → production_contracts (1 PC par opération)

Chaque PC ainsi créé porte :
  - origin_kind = 'flux_contract'
  - origin_ref  = contract_id du flux_contract qui le déclenche

Si une opération a déjà un PC (UNIQUE on of_op_id), l'opération est
**ignorée silencieusement** (la distribution est idempotente sur
re-runs).

API :
  - distribute_contracts_at_p3_exit(conn, batch_id, *, tolerances?)
        -> P3DistributionResult
  - get_pcs_for_batch(conn, batch_id) -> list[int]
  - get_pcs_for_contract(conn, contract_id) -> list[int]
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from pilotage_flux.cybernetic.production_contract import (
    PCTolerances,
    build_pc_for_operation,
)


@dataclass(frozen=True)
class P3DistributionResult:
    """Résultat de l'opération de distribution."""
    batch_id: str
    contracts_processed: tuple[str, ...]
    ofs_processed: tuple[str, ...]
    pcs_created: tuple[int, ...]
    candidates_without_of: tuple[str, ...] = field(default_factory=tuple)

    @property
    def n_pcs(self) -> int:
        return len(self.pcs_created)

    @property
    def n_ofs(self) -> int:
        return len(self.ofs_processed)


def distribute_contracts_at_p3_exit(
    conn: sqlite3.Connection,
    batch_id: str,
    *,
    tolerances: PCTolerances | None = None,
) -> P3DistributionResult:
    """Distribue les contrats d'un freeze_batch en PCs au grain op.

    Renvoie un P3DistributionResult listant les contrats traités,
    les OFs concernés, les PCs créés, et les candidats sans OF
    (cas anormal mais signalé).
    """
    rows = conn.execute(
        "SELECT contract_id, version FROM freeze_batch_contracts "
        "WHERE batch_id = ? ORDER BY contract_id",
        (batch_id,),
    ).fetchall()
    if not rows:
        raise ValueError(f"freeze_batch '{batch_id}' inexistant ou vide")

    contracts_seen: list[str] = []
    ofs_seen: set[str] = set()
    pcs_created: list[int] = []
    cands_without_of: list[str] = []

    for r in rows:
        contract_id = r["contract_id"]
        version = int(r["version"])
        contracts_seen.append(contract_id)

        # Candidats référencés par cette version
        cand_rows = conn.execute(
            "SELECT candidate_id FROM flux_contract_links "
            "WHERE contract_id = ? AND version = ?",
            (contract_id, version),
        ).fetchall()
        for cr in cand_rows:
            candidate_id = cr["candidate_id"]
            of_rows = conn.execute(
                "SELECT of_id FROM manufacturing_orders "
                "WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchall()
            if not of_rows:
                cands_without_of.append(candidate_id)
                continue
            for ofr in of_rows:
                of_id = ofr["of_id"]
                if of_id in ofs_seen:
                    continue
                ofs_seen.add(of_id)
                # Saute les opérations déjà couvertes par un PC
                pcs_for_of = _build_pcs_skipping_existing(
                    conn, of_id, contract_id, tolerances,
                )
                pcs_created.extend(pcs_for_of)

    return P3DistributionResult(
        batch_id=batch_id,
        contracts_processed=tuple(contracts_seen),
        ofs_processed=tuple(sorted(ofs_seen)),
        pcs_created=tuple(pcs_created),
        candidates_without_of=tuple(cands_without_of),
    )


def _build_pcs_skipping_existing(
    conn: sqlite3.Connection,
    of_id: str,
    contract_id: str,
    tolerances: PCTolerances | None,
) -> list[int]:
    """Construit les PCs pour les opérations sans PC existant.

    La table impose UNIQUE(of_op_id) ; on filtre en amont pour rester
    idempotent sans casser sur IntegrityError.
    """
    op_rows = conn.execute(
        """
        SELECT op.of_op_id FROM order_operations op
        WHERE op.of_id = ?
        AND NOT EXISTS (
            SELECT 1 FROM production_contracts pc
            WHERE pc.of_op_id = op.of_op_id
        )
        ORDER BY op.sequence_idx
        """,
        (of_id,),
    ).fetchall()
    if not op_rows:
        return []

    created: list[int] = []
    for r in op_rows:
        created.append(
            build_pc_for_operation(
                conn, int(r["of_op_id"]),
                origin_kind="flux_contract",
                origin_ref=contract_id,
                tolerances=tolerances,
            )
        )
    return created


def get_pcs_for_batch(
    conn: sqlite3.Connection, batch_id: str,
) -> list[int]:
    """Renvoie tous les pc_id rattachés à un freeze_batch.

    Lien : production_contracts.origin_ref IN
           (contracts du batch) AND origin_kind = 'flux_contract'.
    """
    rows = conn.execute(
        """
        SELECT DISTINCT pc.pc_id
        FROM production_contracts pc
        JOIN freeze_batch_contracts fbc
          ON fbc.contract_id = pc.origin_ref
        WHERE fbc.batch_id = ? AND pc.origin_kind = 'flux_contract'
        ORDER BY pc.pc_id
        """,
        (batch_id,),
    ).fetchall()
    return [int(r["pc_id"]) for r in rows]


def get_pcs_for_contract(
    conn: sqlite3.Connection, contract_id: str,
) -> list[int]:
    rows = conn.execute(
        """
        SELECT pc_id FROM production_contracts
        WHERE origin_kind = 'flux_contract' AND origin_ref = ?
        ORDER BY pc_id
        """,
        (contract_id,),
    ).fetchall()
    return [int(r["pc_id"]) for r in rows]
