"""Test d'acceptation V1 - golden path bout-en-bout multi-niveau.

Reproduit le scenario complet du cadrage v2 :
  - Demande multi-article (SO multi-niveau)
  - Aplatissement BOM + pegging
  - P1 (legacy creation OF)
  - P2 avec moteur de regles + risk_debt
  - Contrat de flux versionne + lissage + coherence
  - P3 freeze (tranche gelee immuable)
  - P3 inverse forme A (retour negociable)
  - P3 inverse forme B (fragment)

Verifie tous les invariants V1 :
  - Aucun hardcoding metier
  - Tracabilite end-to-end (event_store + zone_transitions + gate_decisions)
  - Conservation des quantites (fragmentation)
  - Immutabilite (tranche gelee)
  - Versioning (contrats)
  - Pegging multi-niveau (chaine demande -> composants)
"""

from pathlib import Path

import pytest

from pilotage_flux.aps import compute_candidates, get_pegging_chain
from pilotage_flux.db import db_session
from pilotage_flux.flux import (
    compute_coherence,
    compute_smoothing,
    create_contract,
    fetch_contract,
    fetch_freeze_batch,
    get_batch_contracts,
)
from pilotage_flux.gates import (
    DECISION_FREEZE,
    fragment_of,
    get_lineage,
    return_to_negociable,
    run_p1_promotion,
    run_p2_on_libre_zone,
    run_p3_freeze,
)
from pilotage_flux.importers import import_referentials
from pilotage_flux.mes import launch_of
from pilotage_flux.risk_debt import extinguish_risk_debt, list_risk_debts
from pilotage_flux.zones import ZONE_GELEE, ZONE_NEGOCIABLE, current_zone


def test_v1_golden_path_end_to_end(
    tmp_db: Path, fixtures_v1_dir: Path
) -> None:
    """Golden path V1 : demande → P1 → P2 → contrat → P3 → P3 inverse."""

    # ============================================================
    # 1. IMPORT + CBN multi-niveau + pegging
    # ============================================================
    with db_session(tmp_db) as conn:
        import_referentials(conn, fixtures_v1_dir)
        compute_candidates(conn)

        candidates = conn.execute(
            "SELECT candidate_id, article_id, quantity FROM candidate_orders "
            "ORDER BY candidate_id"
        ).fetchall()
    # SO-001 (100 ART-A) + SO-002 (50 ART-A) avec BOM multi-niveau
    # -> 2 ART-A + 2 SEMI-1 = 4 candidates
    assert len(candidates) == 4
    by_article = {}
    for c in candidates:
        by_article.setdefault(c["article_id"], 0)
        by_article[c["article_id"]] += c["quantity"]
    assert by_article == {"ART-A": 150, "SEMI-1": 150}

    # Verifie pegging multi-niveau pour SO-001
    with db_session(tmp_db) as conn:
        chain = get_pegging_chain(conn, "sales_order", "SO-001")
    # SO-001 -> CND(ART-A) -> {CND(SEMI-1) -> COMP-X, COMP-Y}
    quantities = {(l.target_type, l.article_id, l.quantity) for l in chain}
    assert ("candidate_order", "ART-A", 100.0) in quantities
    assert ("candidate_order", "SEMI-1", 100.0) in quantities
    assert ("component", "COMP-X", 200.0) in quantities  # 2 par SEMI-1
    assert ("component", "COMP-Y", 100.0) in quantities

    # ============================================================
    # 2. P1 legacy : creation OF
    # ============================================================
    with db_session(tmp_db) as conn:
        outcome = run_p1_promotion(conn)
    assert len(outcome.ofs_created) == 4

    # ============================================================
    # 3. P2 : 5 criteres + creation risk_debts
    # ============================================================
    with db_session(tmp_db) as conn:
        batch = run_p2_on_libre_zone(conn)
        debts_open = list_risk_debts(conn, status="open")
    # Tous les candidates sortent en PASS_WITH_RISK (R-P2-05 composants V1.3)
    assert batch.passed_with_risk == 4
    assert batch.passed == 0
    assert batch.blocked == 0
    assert len(debts_open) == 4

    # Zones apres P2 : tous en negociable
    with db_session(tmp_db) as conn:
        zone_counts = conn.execute(
            "SELECT zone, COUNT(*) AS n FROM candidate_orders GROUP BY zone"
        ).fetchall()
    zones_by_count = {r["zone"]: r["n"] for r in zone_counts}
    assert zones_by_count == {"negociable": 4}

    # ============================================================
    # 4. Contrat de flux + coherence + lissage
    # ============================================================
    with db_session(tmp_db) as conn:
        cids = [
            r["candidate_id"]
            for r in conn.execute(
                "SELECT candidate_id FROM candidate_orders ORDER BY candidate_id"
            )
        ]
        contract = create_contract(
            conn,
            horizon_label="2026-W27",
            horizon_start="2026-07-06",
            horizon_end="2026-07-12",
            candidate_ids=cids,
        )
        report = compute_coherence(conn, contract.contract_id)
        smoothing = compute_smoothing(conn, contract.contract_id)
    assert contract.current_version == 1
    assert report.overall_ok is True  # charge OK sur l'horizon W27
    # Lissage : 4 candidates etales sur 7 jours
    assert len(smoothing) == 4
    assert smoothing[0].offset_minutes == 0
    assert smoothing[3].offset_minutes > smoothing[0].offset_minutes

    # ============================================================
    # 5. P3 BLOQUE car risk_debts ouvertes
    # ============================================================
    with db_session(tmp_db) as conn:
        result = run_p3_freeze(conn, contract.contract_id)
    assert result.decision == "RENEGOTIATE"
    blocked_debt = next(c for c in result.criteria if c.rule_id == "R-P3-03")
    assert blocked_debt.outcome == "BLOCK"

    # ============================================================
    # 6. Extinction des risk_debts + P3 freeze OK
    # ============================================================
    with db_session(tmp_db) as conn:
        for d in list_risk_debts(conn, status="open"):
            extinguish_risk_debt(
                conn, d.risk_debt_id, reason="composants confirmes par achats"
            )
        result_freeze = run_p3_freeze(conn, contract.contract_id)
        contract_after = fetch_contract(conn, contract.contract_id)
        batch_freeze = fetch_freeze_batch(conn, result_freeze.batch_id)
        batch_contracts = get_batch_contracts(conn, result_freeze.batch_id)
    assert result_freeze.decision == DECISION_FREEZE
    assert contract_after.status == "frozen"
    assert batch_freeze.contract_count == 1
    assert batch_freeze.candidate_count == 4
    assert batch_freeze.total_quantity == 300.0
    # Snapshot fige
    assert len(batch_contracts) == 1
    assert batch_contracts[0].version == 1

    # Zones apres freeze : tous en gelee
    with db_session(tmp_db) as conn:
        zone_counts = conn.execute(
            "SELECT zone, COUNT(*) AS n FROM candidate_orders GROUP BY zone"
        ).fetchall()
    zones_by_count = {r["zone"]: r["n"] for r in zone_counts}
    assert zones_by_count == {"gelee": 4}

    # ============================================================
    # 7. P3 inverse Forme A : renegociation d'un candidate
    # ============================================================
    with db_session(tmp_db) as conn:
        cand_a = conn.execute(
            "SELECT candidate_id FROM candidate_orders "
            "WHERE article_id = 'ART-A' AND quantity = 50"
        ).fetchone()["candidate_id"]
        return_result = return_to_negociable(
            conn, cand_a, reason="renegociation client - lot 50"
        )
        zone_a = current_zone(conn, cand_a)
        cancelled_of = conn.execute(
            "SELECT status FROM manufacturing_orders WHERE of_id = ?",
            (return_result.cancelled_of_id,),
        ).fetchone()
    assert zone_a == ZONE_NEGOCIABLE
    assert cancelled_of["status"] == "cancelled"

    # ============================================================
    # 8. P3 inverse Forme B : fragmentation d'un OF lance
    # ============================================================
    with db_session(tmp_db) as conn:
        # Choisir un OF encore actif (pas le cancelled)
        of_to_launch = conn.execute(
            """
            SELECT of_id, quantity FROM manufacturing_orders
            WHERE status = 'created' LIMIT 1
            """
        ).fetchone()
        launch_of(conn, of_to_launch["of_id"])
        qty_before = of_to_launch["quantity"]
        fragment_qty = qty_before / 4  # fragmente 25%
        frag_result = fragment_of(
            conn,
            of_to_launch["of_id"],
            fragment_quantity=fragment_qty,
            reason="urgence partielle",
        )
        # Verifie conservation quantite
        source_after = conn.execute(
            "SELECT quantity FROM manufacturing_orders WHERE of_id = ?",
            (of_to_launch["of_id"],),
        ).fetchone()["quantity"]
        fragment_qty_db = conn.execute(
            "SELECT quantity, parent_of_id FROM manufacturing_orders WHERE of_id = ?",
            (frag_result.fragment_of_id,),
        ).fetchone()
        lineage = get_lineage(conn, frag_result.fragment_of_id)
    assert source_after + fragment_qty_db["quantity"] == qty_before
    assert fragment_qty_db["parent_of_id"] == of_to_launch["of_id"]
    # Filiation : remonte au parent
    of_ids_in_lineage = [n.of_id for n in lineage]
    assert of_to_launch["of_id"] in of_ids_in_lineage
    assert frag_result.fragment_of_id in of_ids_in_lineage

    # ============================================================
    # 9. Tracabilite : tous evenements presents dans event_store
    # ============================================================
    with db_session(tmp_db) as conn:
        event_types = conn.execute(
            "SELECT DISTINCT event_type FROM event_store ORDER BY event_type"
        ).fetchall()
        all_types = {r["event_type"] for r in event_types}
    assert {
        "OF_CREATED",
        "OF_LAUNCHED",
        "OF_CANCELLED",
        "OF_FRAGMENTED",
        "OF_RETURNED_NEGOCIABLE",
        "GATE_DECISION",
    }.issubset(all_types), f"Manquants : {all_types}"

    # ============================================================
    # 10. Gouvernance : decisions tracees dans gate_decisions_v1
    # ============================================================
    with db_session(tmp_db) as conn:
        decisions = conn.execute(
            "SELECT gate, decision, COUNT(*) AS n FROM gate_decisions_v1 "
            "GROUP BY gate, decision"
        ).fetchall()
        d_map = {(r["gate"], r["decision"]): r["n"] for r in decisions}
    # P2 : 4 PASS_WITH_RISK ; P3 : 1 RENEGOTIATE (avant debts) + 1 FREEZE (apres)
    assert d_map.get(("P2", "PASS_WITH_RISK"), 0) == 4
    assert d_map.get(("P3", "FREEZE"), 0) == 1
    assert d_map.get(("P3", "RENEGOTIATE"), 0) == 1

    # ============================================================
    # 11. Versioning du contrat : intact car frozen empeche modif
    # ============================================================
    with db_session(tmp_db) as conn:
        versions = conn.execute(
            "SELECT version FROM flux_contract_versions WHERE contract_id = ? "
            "ORDER BY version",
            (contract.contract_id,),
        ).fetchall()
    assert [v["version"] for v in versions] == [1]
    # Le contrat reste a v1 ; P3 inverse n'a pas modifie le contrat lui-meme

    # ============================================================
    # 12. Risk_debts : 4 extinctes (preuve qu'extinction prerequis P3)
    # ============================================================
    with db_session(tmp_db) as conn:
        extinct_debts = list_risk_debts(conn, status="extinct")
    assert len(extinct_debts) == 4
    assert all(d.extinction_reason == "composants confirmes par achats" for d in extinct_debts)
