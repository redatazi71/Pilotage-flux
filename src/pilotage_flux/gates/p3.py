"""Porte P3 (négociable -> gelée) - cadrage §6 / §17.

Évalue 4 critères P3 sur un contrat de flux candidat au freeze :

  R-P3-01 : contrat coherent (current_version is_coherent)
  R-P3-02 : tous candidates en zone negociable
  R-P3-03 : aucune risk_debt ouverte
  R-P3-04 : pas de tranche gelee chevauchante deja existante

Decisions :
  FREEZE          : tous criteres PASS -> creation tranche gelee + zone gelee
  PARTIAL_FREEZE  : reserve V2 (pour l'instant traite comme RENEGOTIATE)
  RENEGOTIATE    : au moins un critere BLOCK -> reste en draft/coherent
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from pilotage_flux.events import EventType, append_event
from pilotage_flux.flux import (
    create_freeze_batch,
    fetch_contract,
    fetch_version,
    get_candidates_in_version,
    overlapping_freeze_batches,
)
from pilotage_flux.risk_debt import has_open_debt
from pilotage_flux.zones import (
    ZONE_GELEE,
    ZONE_NEGOCIABLE,
    move_candidate_to_zone,
)


DECISION_FREEZE = "FREEZE"
DECISION_PARTIAL_FREEZE = "PARTIAL_FREEZE"
DECISION_RENEGOTIATE = "RENEGOTIATE"


@dataclass(frozen=True)
class P3CriterionResult:
    rule_id: str
    criterion: str
    outcome: str       # PASS | BLOCK
    explanation: str


@dataclass
class P3Result:
    contract_id: str
    decision: str
    criteria: list[P3CriterionResult] = field(default_factory=list)
    batch_id: str | None = None

    @property
    def passed(self) -> bool:
        return all(c.outcome == "PASS" for c in self.criteria)


def _crit(rule_id: str, criterion: str, ok: bool, explanation: str) -> P3CriterionResult:
    return P3CriterionResult(
        rule_id=rule_id,
        criterion=criterion,
        outcome="PASS" if ok else "BLOCK",
        explanation=explanation,
    )


def evaluate_p3_for_contract(
    conn: sqlite3.Connection,
    contract_id: str,
) -> list[P3CriterionResult]:
    """Évalue les 4 critères P3 sur un contrat. Pas d'effet de bord."""
    contract = fetch_contract(conn, contract_id)
    if contract is None:
        return [_crit("R-P3-00", "contract_exists", False, f"Contrat {contract_id} introuvable")]

    ver = fetch_version(conn, contract_id, contract.current_version)
    candidates = get_candidates_in_version(conn, contract_id, contract.current_version)

    # R-P3-01
    coherence_ok = bool(ver and ver.is_coherent)
    r1 = _crit(
        "R-P3-01",
        "contract_coherence",
        coherence_ok,
        "Version courante coherente" if coherence_ok
        else "Version courante non coherente ou non verifiee",
    )

    # R-P3-02
    bad_zone = [c for c in candidates if c["zone"] != ZONE_NEGOCIABLE]
    r2 = _crit(
        "R-P3-02",
        "candidates_negociable",
        not bad_zone,
        "Tous candidates en zone negociable" if not bad_zone
        else f"Candidates pas en negociable : {[c['candidate_id'] for c in bad_zone]}",
    )

    # R-P3-03
    debt_candidates = [c["candidate_id"] for c in candidates if has_open_debt(conn, c["candidate_id"])]
    r3 = _crit(
        "R-P3-03",
        "no_open_risk_debts",
        not debt_candidates,
        "Aucune risk_debt ouverte" if not debt_candidates
        else f"Risk_debts ouvertes sur : {debt_candidates}",
    )

    # R-P3-04
    overlapping = overlapping_freeze_batches(
        conn, contract.horizon_start, contract.horizon_end
    )
    r4 = _crit(
        "R-P3-04",
        "no_overlapping_freeze",
        not overlapping,
        "Pas de tranche gelee chevauchante" if not overlapping
        else f"Tranches chevauchantes : {[b.batch_id for b in overlapping]}",
    )

    return [r1, r2, r3, r4]


def run_p3_freeze(
    conn: sqlite3.Connection,
    contract_id: str,
    *,
    cycle_id: str | None = None,
    actor: str = "gate.p3",
) -> P3Result:
    """Évalue P3 et freeze le contrat si tous les criteres passent.

    En cas de FREEZE :
      - Crée une tranche gelée immuable
      - Passe tous candidates en zone 'gelee'
      - Met le contrat en statut 'frozen'
      - Émet un event GATE_DECISION (P3 FREEZE) dans event_store
    """
    conn.execute("BEGIN")
    try:
        contract = fetch_contract(conn, contract_id)
        if contract is None:
            conn.execute("ROLLBACK")
            raise ValueError(f"Contrat inconnu : {contract_id}")

        criteria = evaluate_p3_for_contract(conn, contract_id)
        all_pass = all(c.outcome == "PASS" for c in criteria)
        decision = DECISION_FREEZE if all_pass else DECISION_RENEGOTIATE

        result = P3Result(
            contract_id=contract_id,
            decision=decision,
            criteria=criteria,
            batch_id=None,
        )

        explanation = "; ".join(f"{c.rule_id}={c.outcome}" for c in criteria)
        conn.execute(
            """
            INSERT INTO gate_decisions_v1
                (gate, subject_type, subject_id, cycle_id, decision,
                 risk_count, explanation)
            VALUES ('P3', 'flux_contract', ?, ?, ?, 0, ?)
            """,
            (contract_id, cycle_id, decision, explanation),
        )

        if not all_pass:
            conn.execute("COMMIT")
            return result

        # FREEZE -> creation tranche gelee + transition zones
        event_id = append_event(
            conn,
            aggregate_type="flux_contract",
            aggregate_id=contract_id,
            event_type=EventType.GATE_DECISION,
            payload={
                "gate": "P3",
                "decision": "FREEZE",
                "version": contract.current_version,
            },
            actor=actor,
            source_module="gate.p3",
        )

        batch = create_freeze_batch(
            conn,
            contracts=[(contract_id, contract.current_version)],
            horizon_start=contract.horizon_start,
            horizon_end=contract.horizon_end,
            decision=DECISION_FREEZE,
            cycle_id=cycle_id,
            explanation=explanation,
            event_id=event_id,
        )

        # Transition zone negociable -> gelee pour tous les candidates
        candidates = get_candidates_in_version(
            conn, contract_id, contract.current_version
        )
        for cand in candidates:
            move_candidate_to_zone(
                conn,
                cand["candidate_id"],
                ZONE_GELEE,
                decision=DECISION_FREEZE,
                rule_ref="gate.p3",
                explanation=f"Freeze batch {batch.batch_id}",
                cycle_id=cycle_id,
                actor=actor,
                event_id=event_id,
            )

        # Statut du contrat passe a 'frozen'
        conn.execute(
            "UPDATE flux_contracts SET status = 'frozen', updated_at = datetime('now') "
            "WHERE contract_id = ?",
            (contract_id,),
        )

        result.batch_id = batch.batch_id
        conn.execute("COMMIT")
        return result
    except Exception:
        conn.execute("ROLLBACK")
        raise
