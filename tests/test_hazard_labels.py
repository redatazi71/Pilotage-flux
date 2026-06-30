"""Hazards C.1 — étiquetage causal racine_id / categorie_code."""

from __future__ import annotations

import pytest

from pilotage_flux.comparative.scenario import (
    HAZARD_BREAKDOWN,
    HAZARD_LOGISTIC_DELAY,
    HAZARD_PO_DELAY,
    HAZARD_QUALITY_NC,
    HAZARD_URGENT_ORDER,
    HazardEvent,
)
from pilotage_flux.cybernetic.macrs.hazard_labels import (
    HAZARD_TO_CATEGORIE,
    HAZARD_TO_RACINE,
    default_categorie_for,
    default_racine_for,
    labeled_hazard,
    resolve_racine,
)
from pilotage_flux.db import db_session


# ---------------------------------------------------------------------
# Mapping canonique
# ---------------------------------------------------------------------

def test_hazard_to_racine_covers_all_five_hazards() -> None:
    assert set(HAZARD_TO_RACINE) == {
        HAZARD_BREAKDOWN, HAZARD_QUALITY_NC, HAZARD_PO_DELAY,
        HAZARD_URGENT_ORDER, HAZARD_LOGISTIC_DELAY,
    }


def test_hazard_to_categorie_covers_all_five_hazards() -> None:
    assert set(HAZARD_TO_CATEGORIE) == set(HAZARD_TO_RACINE)


def test_canonical_mappings_doctrinal() -> None:
    """Mapping doctrinal cadrage v1.3 (matrice causale) :
       breakdown_ws   → R030 Panne (machine)        → Op
       quality_nc     → R039 NC produit interne    → Qual
       po_delay       → R011 Retard livraison      → Mat
       urgent_order   → R005 Avance de commande    → Temp
       logistic_delay → R019 Incident interne      → Op"""
    assert HAZARD_TO_RACINE[HAZARD_BREAKDOWN] == "R030"
    assert HAZARD_TO_CATEGORIE[HAZARD_BREAKDOWN] == "Op"
    assert HAZARD_TO_RACINE[HAZARD_QUALITY_NC] == "R039"
    assert HAZARD_TO_CATEGORIE[HAZARD_QUALITY_NC] == "Qual"
    assert HAZARD_TO_RACINE[HAZARD_PO_DELAY] == "R011"
    assert HAZARD_TO_CATEGORIE[HAZARD_PO_DELAY] == "Mat"
    assert HAZARD_TO_RACINE[HAZARD_URGENT_ORDER] == "R005"
    assert HAZARD_TO_CATEGORIE[HAZARD_URGENT_ORDER] == "Temp"
    assert HAZARD_TO_RACINE[HAZARD_LOGISTIC_DELAY] == "R019"
    assert HAZARD_TO_CATEGORIE[HAZARD_LOGISTIC_DELAY] == "Op"


@pytest.mark.parametrize("kind, expected_racine", [
    (HAZARD_BREAKDOWN, "R030"),
    (HAZARD_QUALITY_NC, "R039"),
    (HAZARD_PO_DELAY, "R011"),
    (HAZARD_URGENT_ORDER, "R005"),
    (HAZARD_LOGISTIC_DELAY, "R019"),
])
def test_default_racine_for(kind: str, expected_racine: str) -> None:
    assert default_racine_for(kind) == expected_racine


def test_default_racine_for_unknown_kind_returns_none() -> None:
    assert default_racine_for("unknown_kind") is None
    assert default_categorie_for("unknown_kind") is None


# ---------------------------------------------------------------------
# HazardEvent : backward compatibility
# ---------------------------------------------------------------------

def test_hazard_event_backward_compatible_without_labels() -> None:
    """Les anciens HazardEvent (sans racine_id/categorie_code) restent
    valides — les champs sont optionnels (default None)."""
    h = HazardEvent(day=3, kind=HAZARD_BREAKDOWN, payload={"ws": "WS-1"})
    assert h.racine_id is None
    assert h.categorie_code is None


def test_hazard_event_accepts_explicit_labels() -> None:
    h = HazardEvent(
        day=5, kind=HAZARD_QUALITY_NC, payload={"qty": 10},
        racine_id="R040", categorie_code="Qual",
    )
    assert h.racine_id == "R040"
    assert h.categorie_code == "Qual"


# ---------------------------------------------------------------------
# resolve_racine — priorité explicit > params > mapping
# ---------------------------------------------------------------------

def test_resolve_racine_falls_back_to_canonical_mapping(tmp_db) -> None:
    h = HazardEvent(day=3, kind=HAZARD_BREAKDOWN, payload={})
    with db_session(tmp_db) as conn:
        racine, cat = resolve_racine(h, conn=conn)
        assert racine == "R030"
        assert cat == "Op"


def test_resolve_racine_explicit_overrides_mapping() -> None:
    h = HazardEvent(
        day=3, kind=HAZARD_BREAKDOWN, payload={},
        racine_id="R031", categorie_code="Cap",
    )
    racine, cat = resolve_racine(h)
    assert racine == "R031"
    assert cat == "Cap"


def test_resolve_racine_parameter_override(tmp_db) -> None:
    """Un override paramétré dans `parameters` est utilisé si
    l'événement ne porte pas d'étiquettes explicites."""
    h = HazardEvent(day=3, kind=HAZARD_BREAKDOWN, payload={})
    with db_session(tmp_db) as conn:
        # Surcharge R030 → R031
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_text) "
            "VALUES ('global', NULL, 'hazard_racine_breakdown_ws', 'R031')"
        )
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_text) "
            "VALUES ('global', NULL, 'hazard_categorie_breakdown_ws', 'Cap')"
        )
        racine, cat = resolve_racine(h, conn=conn)
        assert racine == "R031"
        assert cat == "Cap"


def test_resolve_racine_explicit_beats_parameter(tmp_db) -> None:
    """Si l'événement porte ses propres labels, ils l'emportent sur
    les params (priorité maximale)."""
    h = HazardEvent(
        day=3, kind=HAZARD_BREAKDOWN, payload={},
        racine_id="R028", categorie_code="Qual",
    )
    with db_session(tmp_db) as conn:
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_text) "
            "VALUES ('global', NULL, 'hazard_racine_breakdown_ws', 'R031')"
        )
        racine, cat = resolve_racine(h, conn=conn)
        assert racine == "R028"
        assert cat == "Qual"


def test_resolve_racine_no_conn_uses_canonical() -> None:
    """Sans conn, la résolution est purement par mapping in-memory."""
    h = HazardEvent(day=3, kind=HAZARD_QUALITY_NC, payload={})
    racine, cat = resolve_racine(h)
    assert racine == "R039"
    assert cat == "Qual"


def test_resolve_racine_unknown_kind_returns_none(tmp_db) -> None:
    h = HazardEvent(day=3, kind="weird_kind", payload={})
    with db_session(tmp_db) as conn:
        racine, cat = resolve_racine(h, conn=conn)
        assert racine is None
        assert cat is None


# ---------------------------------------------------------------------
# labeled_hazard factory
# ---------------------------------------------------------------------

def test_labeled_hazard_factory_auto_resolves() -> None:
    h = labeled_hazard(
        day=5, kind=HAZARD_LOGISTIC_DELAY, payload={"ws": "WS-2"},
    )
    assert h.kind == HAZARD_LOGISTIC_DELAY
    assert h.racine_id == "R019"
    assert h.categorie_code == "Op"
    assert h.payload == {"ws": "WS-2"}


def test_labeled_hazard_explicit_overrides() -> None:
    h = labeled_hazard(
        day=5, kind=HAZARD_BREAKDOWN, payload={},
        racine_id="R031", categorie_code="Cap",
    )
    assert h.racine_id == "R031"
    assert h.categorie_code == "Cap"


def test_labeled_hazard_unknown_kind_leaves_labels_none() -> None:
    """Un kind inconnu → factory ne pose pas de label fictif."""
    h = labeled_hazard(day=5, kind="unknown", payload={})
    assert h.racine_id is None
    assert h.categorie_code is None
