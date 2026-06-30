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
