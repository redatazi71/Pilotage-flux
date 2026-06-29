"""Test d'acceptation V0 - golden path bout-en-bout.

Execute le scenario de demonstration du document de cadrage §21 bis.4 :
demande -> CBN -> contrats OF (P1) -> execution MES -> cloture P4 ->
reconstruction event-sourcee. Si ce test passe, le V0 satisfait son
critere de succes n°1 (§21 bis.5).
"""

from pathlib import Path

import pytest

from pilotage_flux.db import db_session
from pilotage_flux.events import EventType, reconstruct_of
from pilotage_flux.gates import run_p1_promotion
from pilotage_flux.importers import import_referentials
from pilotage_flux.mes import close_of, finish_operation, launch_of, start_operation
from pilotage_flux.visualization import of_detail_view, workstation_view


def test_golden_path_end_to_end(tmp_db: Path, fixtures_dir: Path) -> None:
    """Demande -> CBN -> P1 -> MES -> P4 -> reconstruction event-sourcee."""

    # ============================================================
    # 1. IMPORT des referentiels reels
    # ============================================================
    with db_session(tmp_db) as conn:
        results = import_referentials(conn, fixtures_dir)
        counts = {r.table: r.rows_inserted for r in results}
    assert counts["articles"] == 3
    assert counts["workstations"] == 3
    assert counts["sales_orders"] == 2

    # ============================================================
    # 2. APS : CBN + P1 (creation contrats OF)
    # ============================================================
    with db_session(tmp_db) as conn:
        outcome = run_p1_promotion(conn)
    assert len(outcome.candidates_created) == 2
    assert len(outcome.ofs_created) == 2
    # WS-2 doit etre identifie comme goulot (capacity_factor 0.80 + charge 450 min)
    overloaded = [w.workstation_id for w in outcome.workstation_load if w.is_overloaded]
    assert overloaded == ["WS-2"], (
        f"WS-2 doit etre identifie comme goulot dynamique, surcharge: {overloaded}"
    )

    of_ids = sorted(o.of_id for o in outcome.ofs_created)
    of_principal = of_ids[0]  # OF-0001

    # ============================================================
    # 3. MES : execution complete de OF-0001
    # ============================================================
    with db_session(tmp_db) as conn:
        launch_of(conn, of_principal)
        ops = conn.execute(
            "SELECT of_op_id, sequence_idx FROM order_operations "
            "WHERE of_id = ? ORDER BY sequence_idx",
            (of_principal,),
        ).fetchall()
        for op in ops:
            start_operation(conn, op["of_op_id"])
            finish_operation(conn, op["of_op_id"], qty_good=95.0, qty_scrap=5.0)
        close_result = close_of(conn, of_principal)

    # ============================================================
    # 4. Verifications etat final
    # ============================================================
    with db_session(tmp_db) as conn:
        detail = of_detail_view(conn, of_principal)
        of_2_detail = of_detail_view(conn, of_ids[1])
    assert detail is not None
    assert detail.status == "closed"
    assert detail.qty_good == 95.0
    assert detail.qty_scrap == 5.0
    assert len(detail.operations) == 3
    assert all(op.status == "done" for op in detail.operations)
    # Chaque op a 2 declarations terrain (start + finish)
    assert all(len(op.declarations) == 2 for op in detail.operations)
    # Le 2e OF n'a pas demarre
    assert of_2_detail is not None
    assert of_2_detail.status == "created"

    # ============================================================
    # 5. Critere de succes n°3 du V0 : reconstruction event-sourcee
    #    L'etat complet doit etre reconstruisible depuis l'event_store seul
    # ============================================================
    with db_session(tmp_db) as conn:
        state = reconstruct_of(conn, of_principal)
    # OF_CREATED + OF_LAUNCHED + 3*(OP_STARTED+OP_FINISHED) + OF_CLOSED = 9 events
    assert state.event_count == 9
    assert state.status == "closed"
    assert state.article_id == "ART-A"
    assert state.quantity == 100.0
    assert state.qty_good == 95.0
    assert state.qty_scrap == 5.0
    assert len(state.operations_started) == 3
    assert len(state.operations_finished) == 3

    # ============================================================
    # 6. Critere de succes n°2 du V0 : data-driven prouve
    #    Modifier une capacite en base change le verdict de surcharge
    # ============================================================
    with db_session(tmp_db) as conn:
        # On debloque WS-2
        conn.execute(
            "UPDATE parameters SET value_num = 1.20 "
            "WHERE scope='workstation' AND scope_ref='WS-2' AND name='capacity_factor'"
        )
        # Force recalcul - le 2e OF n'a pas encore demarre, l'evaluation porte sur lui
        from pilotage_flux.aps import compute_load_by_workstation
        loads = compute_load_by_workstation(conn)
        by_ws = {w.workstation_id: w for w in loads}
    # Capacite WS-2 monte de 384 a 576 min, charge totale (150 OF) = 450 min < 576
    assert by_ws["WS-2"].is_overloaded is False, (
        "Apres relevement parametre capacity_factor, WS-2 ne doit plus etre surcharge"
    )

    # ============================================================
    # 7. Trace gouvernance : decisions P1 + P4 sont historisees
    # ============================================================
    with db_session(tmp_db) as conn:
        p1_decisions = conn.execute(
            "SELECT COUNT(*) AS n FROM gate_decisions WHERE gate = 'P1'"
        ).fetchone()
        p4_decisions = conn.execute(
            "SELECT COUNT(*) AS n FROM gate_decisions WHERE gate = 'P4'"
        ).fetchone()
        all_events = conn.execute(
            "SELECT event_type, COUNT(*) AS n FROM event_store GROUP BY event_type"
        ).fetchall()
    assert p1_decisions["n"] == 2  # 2 OF crees
    assert p4_decisions["n"] == 1  # 1 OF cloture

    by_type = {e["event_type"]: e["n"] for e in all_events}
    assert by_type[EventType.OF_CREATED.value] == 2
    assert by_type[EventType.OF_LAUNCHED.value] == 1
    assert by_type[EventType.OP_STARTED.value] == 3
    assert by_type[EventType.OP_FINISHED.value] == 3
    assert by_type[EventType.OF_CLOSED.value] == 1

    # ============================================================
    # 8. Visualisation flux physique : WS-1 a une op done + une pending
    # ============================================================
    with db_session(tmp_db) as conn:
        views = workstation_view(conn)
    by_ws_view = {v.workstation_id: v for v in views}
    assert len(by_ws_view["WS-1"].done) == 1, "OF-0001 termine"
    assert len(by_ws_view["WS-1"].pending) == 1, "OF-0002 en attente"

    # FIN - V0 valide
    assert close_result.qty_good == 95.0
