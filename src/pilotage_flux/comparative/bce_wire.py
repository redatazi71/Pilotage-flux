"""Câblage BCE (Boucle Cybernétique Étendue) dans le runner comparatif.

Active la chaîne MACRS Couche 1+2 + moteur Delta + propagation
hazard étiquetée pour les pilotages BCE (OF+EVENT+BCE et
FLUX+EVENT+BCE), sans modifier les pilotages non-BCE qui
bypassent silencieusement la couche cybernétique.

Architecture :

  - **bce_bootstrap(conn)** : seed Couche 1 MACRS (46 racines),
    init 165 cellules INCOMING, seed 6 niveaux moteur Delta.
    Idempotent — sûr d'être appelé plusieurs fois.

  - **bce_apply_hazard_hook(conn, hazard, doctrine, day_iso)** :
    appelé depuis `_apply_hazard` du runner. Si doctrine BCE,
    seed BCE (idempotent) et appelle `emit_hazard` (C.2) qui
    propage à travers MACRS → filtre dual → delta_decision →
    approval_queue. Renvoie un dict KPIs ou None si non-BCE.

  - **bce_kpis(conn)** : extrait les KPIs nervosité segmentés
    N1..N4 (cadrage v1.3) et la distribution L1..L6 (CDC).

  - **run_of_event_bce_doctrine / run_event_bce_doctrine** :
    wrappers qui appellent les runners historiques en surchargeant
    la chaîne `doctrine` pour activer le hook BCE.

Le BCE est **non-invasif** : sans appel à bce_apply_hazard_hook
avec une doctrine BCE, aucune écriture n'a lieu dans les tables
MACRS / delta_decisions / approval_queue de la chaîne BCE.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pilotage_flux.comparative.scenario import (
    BCE_DOCTRINES,
    DOCTRINE_EVENT_BCE,
    DOCTRINE_OF_EVENT_BCE,
    Scenario,
    is_bce_doctrine,
)
from pilotage_flux.cybernetic.delta_engine.levels import (
    seed_default_delta_levels,
)
from pilotage_flux.cybernetic.macrs.couche1 import seed_macrs_layer1
from pilotage_flux.cybernetic.macrs.couche2 import init_cells_from_layer1


def bce_bootstrap(conn: sqlite3.Connection) -> None:
    """Initialise les tables BCE. Idempotent."""
    seed_macrs_layer1(conn)
    init_cells_from_layer1(conn)
    seed_default_delta_levels(conn)


# Mapping pilotage → profil de tolérance doctrinal.
# La doctrine v1.3 prévoit que BCE absorbe plus (favorise N1/N2) et
# replanifie moins, d'où le profil CONSERVATIVE pour les pilotages BCE.
# Les pilotages +EVENT sans BCE gardent les seuils permissifs
# historiques (DEFAULT). Les pilotages pures OF/FLUX n'utilisent pas
# le moteur Delta.
TOLERANCE_DEFAULTS_BY_DOCTRINE: dict[str, dict[str, float]] = {
    # BCE : seuils larges pour absorber (CONSERVATIVE)
    "of_event_bce": {
        "tolerance_threshold_watch": 0.50,
        "tolerance_threshold_correct_local": 1.00,
        "tolerance_threshold_replan_local": 1.50,
        "tolerance_threshold_escalate": 2.00,
        "tolerance_threshold_replan_global": 3.00,
    },
    "event_bce": {
        "tolerance_threshold_watch": 0.50,
        "tolerance_threshold_correct_local": 1.00,
        "tolerance_threshold_replan_local": 1.50,
        "tolerance_threshold_escalate": 2.00,
        "tolerance_threshold_replan_global": 3.00,
    },
}


def get_tolerance_defaults_for_doctrine(
    doctrine: str,
) -> dict[str, float]:
    """Renvoie les seuils par défaut du filtre dual pour un pilotage.

    Si le pilotage est BCE, renvoie les seuils CONSERVATIVE (favorise
    N1/N2). Sinon renvoie un dict vide → le caller utilise ses
    propres defaults historiques.
    """
    return dict(TOLERANCE_DEFAULTS_BY_DOCTRINE.get(doctrine, {}))


def bce_apply_hazard_hook(
    conn: sqlite3.Connection,
    hazard,
    doctrine: str,
    *,
    day_iso: str,
) -> dict | None:
    """Hook BCE appelé après l'application historique d'un hazard.

    Si la doctrine n'est pas BCE, ne fait rien (None).
    Si la doctrine est BCE, garantit que les tables BCE sont
    seedées puis propage le hazard via `emit_hazard`.

    Renvoie un dict de tracé pour `result.hazards_observed`, ou
    None si non-BCE ou si emit_hazard a skippé (kind inconnu).
    """
    if not is_bce_doctrine(doctrine):
        return None
    # Seed idempotent ; n'écrit que si pas déjà fait.
    bce_bootstrap(conn)
    # Import différé : la chaîne emit_hazard dépend de
    # delta_engine.macrs_wiring qui dépend de macrs.couche2.
    from datetime import datetime, timedelta
    from pilotage_flux.cybernetic.macrs.hazard_emission import (
        emit_hazard,
    )
    # day_iso peut être "YYYY-MM-DD HH:MM:SS" ou ISO complet
    occ_str = day_iso.replace(" ", "T")
    occ = datetime.fromisoformat(occ_str)
    decided = (occ + timedelta(minutes=1)).isoformat()
    res = emit_hazard(
        conn, hazard,
        occurred_at=occ.isoformat(),
        decided_at=decided,
        enqueue_if_human=True,
        actor="auto:bce_loop",
    )
    if res.skipped_reason is not None:
        # Même quand le mapping standard est manquant, on tente
        # l'émission zone libre (forecast deviation) si applicable.
        zone_libre_info = _emit_zone_libre_if_applicable(
            conn, hazard, occ.isoformat(), decided,
        )
        trace = {
            "bce_skipped": res.skipped_reason,
            "kind": hazard.kind,
        }
        if zone_libre_info is not None:
            trace.update(zone_libre_info)
        return trace
    cyber = res.cybernetic_decision
    trace = {
        "bce_deviation_id": res.deviation_id,
        "bce_racine_id": res.racine_id,
        "bce_categorie_code": res.categorie_code,
        "bce_base_niveau": cyber.base_niveau if cyber else None,
        "bce_final_niveau": cyber.final_niveau if cyber else None,
        "bce_boost_applied": cyber.boost_applied if cyber else 0,
        "bce_approval_queue_id": (
            cyber.approval_queue_id if cyber else None
        ),
    }
    # Émission complémentaire zone libre si le hazard a un mapping
    # forecast (par ex. URGENT_ORDER, PO_DELAY).
    zone_libre_info = _emit_zone_libre_if_applicable(
        conn, hazard, occ.isoformat(), decided,
    )
    if zone_libre_info is not None:
        trace.update(zone_libre_info)
    return trace


def _emit_zone_libre_if_applicable(
    conn: sqlite3.Connection,
    hazard,
    occurred_at: str,
    decided_at: str,
) -> dict | None:
    """Émet une déviation forecast dédiée zone libre si le hazard
    a un mapping dans HAZARD_TO_FORECAST_RACINE.

    Renvoie un dict de trace ou None si pas de mapping.
    """
    from pilotage_flux.cybernetic.macrs.forecast_emission import (
        emit_forecast_deviation,
    )
    res = emit_forecast_deviation(
        conn, hazard,
        occurred_at=occurred_at, decided_at=decided_at,
    )
    if res.skipped_reason is not None:
        return None
    return {
        "zone_libre_deviation_id": res.deviation_id,
        "zone_libre_racine_id": res.racine_id,
        "zone_libre_categorie_code": res.categorie_code,
        "zone_libre_delta_decision_id": res.delta_decision_id,
    }


def bce_distribute_pcs_after_freeze(
    conn: sqlite3.Connection,
    batch_id: str,
    doctrine: str,
) -> dict | None:
    """Matérialise les PC=(T,Ep,Er,C,O) au grain opération après P3.

    Si la doctrine n'est pas BCE, ne fait rien (None).

    Deux chemins selon le batch :
      - **Batch avec contrats** (FLUX-based : `event_bce`) : utilise
        `distribute_contracts_at_p3_exit` (Goldilocks #5). Le parcours
        est freeze_batch → contracts → candidates → OFs → ops → PCs
        avec origin_kind='flux_contract'.
      - **Batch virtuel** (OF-based : `of_event_bce`) : itère sur les
        manufacturing_orders et appelle `build_pcs_for_of` avec
        origin_kind='candidate' ou 'sales_order'.

    Renvoie {pcs_via, n_pcs, n_ofs} ou None si non-BCE.
    """
    if not is_bce_doctrine(doctrine):
        return None
    bce_bootstrap(conn)
    # Batch a-t-il des contrats liés ?
    has_contracts = conn.execute(
        "SELECT 1 FROM freeze_batch_contracts WHERE batch_id = ? "
        "LIMIT 1",
        (batch_id,),
    ).fetchone() is not None

    if has_contracts:
        # Chemin nominal Goldilocks #5
        from pilotage_flux.cybernetic.p3_distribution import (
            distribute_contracts_at_p3_exit,
        )
        try:
            res = distribute_contracts_at_p3_exit(conn, batch_id)
            return {
                "pcs_via": "flux_contract",
                "n_pcs": res.n_pcs,
                "n_ofs": res.n_ofs,
                "contracts_processed": list(res.contracts_processed),
            }
        except ValueError:
            return {"pcs_via": "flux_contract", "n_pcs": 0,
                    "n_ofs": 0, "error": "empty_batch"}

    # Fallback : batch virtuel (OF+EVENT+BCE)
    from pilotage_flux.cybernetic.production_contract import (
        build_pcs_for_of,
    )
    ofs = conn.execute(
        "SELECT of_id, candidate_id FROM manufacturing_orders "
        "WHERE status NOT IN ('closed', 'cancelled')"
    ).fetchall()
    n_pcs = 0
    n_ofs_processed = 0
    for r in ofs:
        if r["candidate_id"]:
            origin_kind = "candidate"
            origin_ref = r["candidate_id"]
        else:
            origin_kind = "sales_order"
            origin_ref = r["of_id"]
        try:
            pc_ids = build_pcs_for_of(
                conn, r["of_id"],
                origin_kind=origin_kind,
                origin_ref=origin_ref,
            )
            n_pcs += len(pc_ids)
            n_ofs_processed += 1
        except Exception:
            continue
    return {
        "pcs_via": "direct_ofs",
        "n_pcs": n_pcs,
        "n_ofs": n_ofs_processed,
    }


def bce_kpis(conn: sqlite3.Connection) -> dict:
    """KPIs BCE pour intégration au rapport comparatif.

    Distribution decisions par niveau CDC (L1..L6) et par niveau
    cadrage v1.3 (N1..N4 = nervosité segmentée), + compteurs MACRS.
    """
    from pilotage_flux.cybernetic.delta_engine.decisions import (
        count_decisions_by_cadrage_level,
        count_decisions_by_level,
    )
    by_niveau = count_decisions_by_level(conn)
    by_cadrage = count_decisions_by_cadrage_level(conn)
    macrs_active = conn.execute(
        "SELECT COUNT(*) AS n FROM causal_cells WHERE status='ACTIVE'"
    ).fetchone()
    macrs_events = conn.execute(
        "SELECT COUNT(*) AS n FROM causal_events"
    ).fetchone()
    pending = conn.execute(
        "SELECT COUNT(*) AS n FROM approval_queue WHERE status='pending'"
    ).fetchone()
    # Zone libre : decisions liées aux racines forecast R002/R003/R017
    from pilotage_flux.cybernetic.macrs.forecast_emission import (
        count_zone_libre_decisions,
    )
    zl = count_zone_libre_decisions(conn)
    # Compteurs PC (Goldilocks #4 + #5 wiring) — production_contracts
    # matérialisés post-P3.
    pc_total = conn.execute(
        "SELECT COUNT(*) AS n FROM production_contracts"
    ).fetchone()
    pc_by_status_rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM production_contracts "
        "GROUP BY status"
    ).fetchall()
    pc_by_status = {r["status"]: int(r["n"]) for r in pc_by_status_rows}
    return {
        "delta_decisions_by_niveau": by_niveau,
        "delta_decisions_by_cadrage_level": by_cadrage,
        "n_decisions_total": sum(by_niveau.values()),
        "n1_decisions": by_cadrage.get(1, 0),
        "n2_decisions": by_cadrage.get(2, 0),
        "n3_decisions": by_cadrage.get(3, 0),
        "n4_decisions": by_cadrage.get(4, 0),
        "macrs_cells_active": int(macrs_active["n"]) if macrs_active else 0,
        "macrs_events_total": int(macrs_events["n"]) if macrs_events else 0,
        "approval_queue_pending": int(pending["n"]) if pending else 0,
        # Zone libre
        "zone_libre_n_decisions": zl["n_total"],
        "zone_libre_by_racine": zl["by_racine"],
        "zone_libre_by_niveau": zl["by_niveau"],
        # PCs au grain opération (Goldilocks #4 + #5 wiring)
        "pcs_total": int(pc_total["n"]) if pc_total else 0,
        "pcs_by_status": pc_by_status,
    }


# ---------------------------------------------------------------------
# Nouveaux runners BCE — wrappers qui surchargent le `doctrine` passé
# au runner historique pour activer le hook.
# ---------------------------------------------------------------------


def run_of_event_bce_doctrine(
    scenario: Scenario, db_path: Path, *,
    fixtures_dir: Path | None = None,
):
    """Pilotage OF + EVENT + BCE."""
    from pilotage_flux.comparative.runner import (
        DEFAULT_FIXTURES_DIR,
        run_of_event_doctrine,
    )
    fd = fixtures_dir if fixtures_dir is not None else DEFAULT_FIXTURES_DIR
    return run_of_event_doctrine(
        scenario, db_path,
        fixtures_dir=fd,
        doctrine_override=DOCTRINE_OF_EVENT_BCE,
    )


def run_event_bce_doctrine(
    scenario: Scenario, db_path: Path, *,
    fixtures_dir: Path | None = None,
):
    """Pilotage FLUX + EVENT + BCE."""
    from pilotage_flux.comparative.runner import (
        DEFAULT_FIXTURES_DIR,
        run_event_doctrine,
    )
    fd = fixtures_dir if fixtures_dir is not None else DEFAULT_FIXTURES_DIR
    return run_event_doctrine(
        scenario, db_path,
        fixtures_dir=fd,
        doctrine_override=DOCTRINE_EVENT_BCE,
    )
