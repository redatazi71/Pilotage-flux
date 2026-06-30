"""MACRS A.1 — Tests de la Couche 1 (matrice d'incidence statique).

Couvre :
  - 7 catégories Δ canoniques
  - 46 racines R001..R046 (5 domaines, 24 sous-domaines)
  - 165 cellules d'incidence binaire (cf. note doctrinale)
  - Distribution par catégorie : 22/21/26/15/38/14/29
  - Distribution par prédictibilité : 17 forte, 19 moyenne, 10 faible
  - Seed idempotent
"""

from __future__ import annotations

from collections import Counter

import pytest

from pilotage_flux.cybernetic.macrs.couche1 import (
    CATEGORIES,
    RACINES,
    count_incidences,
    get_incidences_for_racine,
    get_racines_for_category,
    list_categories,
    list_racines,
    seed_macrs_layer1,
)
from pilotage_flux.db import db_session


EXPECTED_INCIDENCES_TOTAL = 165
EXPECTED_PER_CATEGORY = {
    "Mat":  22, "Cap":  21, "Op":   26, "Qual": 15,
    "Temp": 38, "Info": 14, "Sync": 29,
}
EXPECTED_PER_DOMAINE = {
    "demande": 9, "approvisionnement": 8, "logistique": 9,
    "production": 12, "qualite": 8,
}
EXPECTED_PER_PREDICT = {"forte": 17, "moyenne": 19, "faible": 10}


def test_categories_canonical_order() -> None:
    codes = [c[0] for c in CATEGORIES]
    assert codes == ["Mat", "Cap", "Op", "Qual", "Temp", "Info", "Sync"]


def test_racines_count_46() -> None:
    assert len(RACINES) == 46
    ids = [r.racine_id for r in RACINES]
    # R001..R046 séquentiels, identifiants stables
    assert ids[0] == "R001"
    assert ids[-1] == "R046"
    assert len(set(ids)) == 46


def test_racines_distribution_by_domain() -> None:
    by_dom = Counter(r.domaine for r in RACINES)
    assert dict(by_dom) == EXPECTED_PER_DOMAINE


def test_racines_distribution_by_predictibilite() -> None:
    by_pred = Counter(r.predictibilite for r in RACINES)
    assert dict(by_pred) == EXPECTED_PER_PREDICT


def test_total_incidences_165() -> None:
    total = sum(len(r.incidences) for r in RACINES)
    assert total == EXPECTED_INCIDENCES_TOTAL


def test_incidences_distribution_by_category() -> None:
    counter = Counter()
    for r in RACINES:
        for c in r.incidences:
            counter[c] += 1
    assert dict(counter) == EXPECTED_PER_CATEGORY


def test_incidences_use_only_known_categories() -> None:
    known = {c[0] for c in CATEGORIES}
    for r in RACINES:
        for c in r.incidences:
            assert c in known, f"{r.racine_id} cite catégorie inconnue '{c}'"


def test_seed_inserts_canonical_counts(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        inserted = seed_macrs_layer1(conn)
        assert inserted == {
            "categories": 7,
            "racines": 46,
            "incidences": EXPECTED_INCIDENCES_TOTAL,
        }
        assert count_incidences(conn) == EXPECTED_INCIDENCES_TOTAL


def test_seed_is_idempotent(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        first = seed_macrs_layer1(conn)
        second = seed_macrs_layer1(conn)
        assert first["incidences"] == EXPECTED_INCIDENCES_TOTAL
        assert second == {"categories": 0, "racines": 0, "incidences": 0}


def test_seed_preserves_predictibilite(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        seed_macrs_layer1(conn)
        # Sample : R002 = Variation conjoncturelle (Forte)
        row = conn.execute(
            "SELECT predictibilite, c1_precurseur, c2_cumulative, c3_aleatoire "
            "FROM macrs_racines WHERE racine_id = 'R002'"
        ).fetchone()
        assert row["predictibilite"] == "forte"
        assert row["c1_precurseur"] == "O"
        assert row["c2_cumulative"] == "O"
        assert row["c3_aleatoire"] == "non"


def test_get_incidences_for_racine_returns_ordered(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        seed_macrs_layer1(conn)
        # R008 — Changement technique — incidences = Mat, Op, Qual, Temp, Info, Sync
        cats = get_incidences_for_racine(conn, "R008")
        assert cats == ["Mat", "Op", "Qual", "Temp", "Info", "Sync"]


def test_get_racines_for_category(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        seed_macrs_layer1(conn)
        # 22 racines causent Mat
        mat_racines = get_racines_for_category(conn, "Mat")
        assert len(mat_racines) == 22
        # 15 racines causent Qual
        qual_racines = get_racines_for_category(conn, "Qual")
        assert len(qual_racines) == 15


def test_list_racines_filter_by_domaine(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        seed_macrs_layer1(conn)
        prod = list_racines(conn, domaine="production")
        assert len(prod) == 12
        # toutes en production
        assert all(r["domaine"] == "production" for r in prod)


def test_list_racines_filter_by_predictibilite(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        seed_macrs_layer1(conn)
        forte = list_racines(conn, predictibilite="forte")
        assert len(forte) == 17


def test_list_categories_returns_seven_in_order(tmp_db) -> None:
    with db_session(tmp_db) as conn:
        seed_macrs_layer1(conn)
        cats = list_categories(conn)
        assert [c["categorie_code"] for c in cats] == [
            "Mat", "Cap", "Op", "Qual", "Temp", "Info", "Sync",
        ]


def test_incidence_uniqueness_constraint(tmp_db) -> None:
    """PRIMARY KEY (racine_id, categorie_code) interdit les doublons."""
    import sqlite3 as _sqlite3
    with db_session(tmp_db) as conn:
        seed_macrs_layer1(conn)
        with pytest.raises(_sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO macrs_incidence "
                "(racine_id, categorie_code) VALUES ('R001', 'Mat')"
            )


def test_no_inactive_couples_in_base(tmp_db) -> None:
    """Aucune cellule ne doit exister pour un couple `incidence = 0`."""
    with db_session(tmp_db) as conn:
        seed_macrs_layer1(conn)
        # R006 = Retard de commande — n'a que Temp + Sync.
        # Mat ne doit PAS exister pour R006.
        row = conn.execute(
            "SELECT 1 FROM macrs_incidence "
            "WHERE racine_id = 'R006' AND categorie_code = 'Mat'"
        ).fetchone()
        assert row is None


def test_racine_R039_nc_produit_interne_six_incidences(tmp_db) -> None:
    """R039 NC produit interne couvre 6 catégories : Mat, Cap, Op, Qual,
    Temp, Sync — vérifie l'exactitude de la transcription sur une racine
    à forte couverture."""
    with db_session(tmp_db) as conn:
        seed_macrs_layer1(conn)
        cats = set(get_incidences_for_racine(conn, "R039"))
        assert cats == {"Mat", "Cap", "Op", "Qual", "Temp", "Sync"}
