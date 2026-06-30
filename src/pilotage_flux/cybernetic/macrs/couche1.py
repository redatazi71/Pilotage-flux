"""MACRS Couche 1 — Matrice d'incidence causale enrichie.

Référence métier : matrice_incidence_causale.md.

Volumétrie : 46 racines (R001..R046) réparties sur 5 domaines et
24 sous-domaines ; 7 catégories Δ ; **165 cellules d'incidence
binaire** (■ dans la matrice).

NB doctrinale : l'intro de matrice_incidence_causale.md annonce
« 175 cellules actives » mais la somme des ■ effectivement marqués
dans les tableaux par domaine (et la synthèse colonne par colonne)
totalise 165 (22 Mat + 21 Cap + 26 Op + 15 Qual + 38 Temp +
14 Info + 29 Sync). On retient 165, cohérent avec le contenu du
document. Toute mise à jour future de la matrice doit ré-aligner
cette constante.

Les identifiants R001..R046 sont **stables** : ne jamais réutiliser un
identifiant supprimé (cf. §4.2 du document de référence).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


# 7 catégories Delta (ordre canonique du document)
CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("Mat",  "Matière",          "Composant requis indisponible ou stock net insuffisant"),
    ("Cap",  "Capacité",         "Charge planifiée supérieure à la capacité disponible"),
    ("Op",   "Opérationnelle",   "Opération bloquée, arrêtée ou terminée après seuil critique"),
    ("Qual", "Qualité",          "Lot bloqué ou quantité non conforme"),
    ("Temp", "Temporelle",       "Jalon dépassé au-delà d'un seuil de zone"),
    ("Info", "Informationnelle", "Événement réel incohérent ou non rapprochable"),
    ("Sync", "Synchronisation",  "Matière, ressource et jalon non alignés simultanément"),
)


@dataclass(frozen=True)
class _Racine:
    racine_id: str
    domaine: str
    sous_domaine: str
    label: str
    incidences: tuple[str, ...]   # codes catégories actives (■)
    c1: str                         # 'O' | 'N'
    c2: str                         # 'O' | 'N' | 'P'
    c3: str                         # 'non' | 'partiel' | 'dominant'
    predictibilite: str             # 'forte' | 'moyenne' | 'faible'
    mecanisme: str
    observabilite: str


# Petits helpers locaux pour la lisibilité
_F, _M, _f = "forte", "moyenne", "faible"


# ---------------------------------------------------------------------
# 46 racines — transcrites depuis la matrice de référence
# ---------------------------------------------------------------------
RACINES: tuple[_Racine, ...] = (
    # ----- 3.1 Demande (9) -----
    _Racine("R001", "demande", "volume", "Pic exceptionnel",
            ("Mat", "Cap", "Temp", "Sync"),
            "O", "N", "partiel", _M,
            "Commande exceptionnelle d'un client, événement marketing, opportunité",
            "Carnet de commandes, prévisions commerciales, signaux marché"),
    _Racine("R002", "demande", "volume", "Variation conjoncturelle",
            ("Mat", "Cap", "Temp"),
            "O", "O", "non", _F,
            "Cycle économique, évolution de marché, changement de positionnement",
            "Moyennes mobiles longues, indicateurs économiques externes"),
    _Racine("R003", "demande", "volume", "Saisonnalité non anticipée",
            ("Mat", "Cap", "Temp"),
            "O", "O", "non", _F,
            "Saisonnalité réelle déviant de la saisonnalité prévue",
            "Historique pluriannuel, décomposition de série temporelle"),
    _Racine("R004", "demande", "mix_produits", "Basculement de référence",
            ("Mat", "Cap", "Op", "Temp", "Sync"),
            "O", "O", "partiel", _M,
            "Substitution client, fin de vie produit, montée d'une nouvelle référence",
            "Mix historique, cannibalisation observable"),
    _Racine("R005", "demande", "timing", "Avance de commande",
            ("Mat", "Cap", "Temp", "Sync"),
            "O", "N", "partiel", _M,
            "Reprogrammation client, urgence opérationnelle aval",
            "Carnet ferme vs prévisionnel, requêtes de modification de date"),
    _Racine("R006", "demande", "timing", "Retard de commande",
            ("Temp", "Sync"),
            "O", "N", "partiel", _M,
            "Décalage de projet client, ajustement de stock client",
            "Carnet ferme vs prévisionnel"),
    _Racine("R007", "demande", "timing", "Modification de date",
            ("Mat", "Cap", "Temp", "Sync"),
            "N", "N", "dominant", _f,
            "Décision client opérationnelle ponctuelle",
            "Fréquence historique des modifications par client"),
    _Racine("R008", "demande", "specifications", "Changement technique",
            ("Mat", "Op", "Qual", "Temp", "Info", "Sync"),
            "O", "N", "partiel", _M,
            "Évolution besoin client, modification réglementaire",
            "Notifications client, ECR/ECO en cours"),
    _Racine("R009", "demande", "annulation_reduction", "Annulation commande",
            ("Cap", "Temp", "Info"),
            "N", "N", "dominant", _f,
            "Décision client, défaut commercial aval, conjoncture",
            "Carnet ferme, alertes commerciales, historique annulations par client"),

    # ----- 3.2 Approvisionnement (8) -----
    _Racine("R010", "approvisionnement", "fournisseur", "Défaillance livraison",
            ("Mat", "Op", "Temp", "Sync"),
            "O", "P", "partiel", _M,
            "Rupture fournisseur, défaillance logistique amont, refus de livraison",
            "Historique OTD fournisseur, alertes ASN, score qualité fournisseur"),
    _Racine("R011", "approvisionnement", "fournisseur", "Retard livraison",
            ("Mat", "Op", "Temp", "Sync"),
            "O", "O", "partiel", _M,
            "Tension capacitaire fournisseur, transport amont dégradé",
            "OTD fournisseur, lead time observé glissant, signaux ASN"),
    _Racine("R012", "approvisionnement", "fournisseur", "Non-conformité qualité",
            ("Mat", "Op", "Qual", "Temp", "Sync"),
            "O", "O", "partiel", _M,
            "Dérive process fournisseur, défaut transport, défaut conception",
            "PPM fournisseur, historique non-conformités, contrôles entrée"),
    _Racine("R013", "approvisionnement", "composant", "Obsolescence",
            ("Mat", "Op", "Temp", "Info"),
            "O", "O", "non", _F,
            "Décision fournisseur, évolution technologique, fin de série",
            "Notifications fournisseur, roadmaps composants, alertes PCN"),
    _Racine("R014", "approvisionnement", "composant", "Rupture amont",
            ("Mat", "Op", "Temp", "Sync"),
            "O", "P", "dominant", _f,
            "Crise géopolitique, catastrophe naturelle, tension marché",
            "Veille marché, indicateurs sectoriels, allocations fournisseurs"),
    _Racine("R015", "approvisionnement", "contrat", "MOQ contraignant",
            ("Mat", "Info"),
            "O", "O", "non", _F,
            "Politique commerciale fournisseur, contraintes process amont",
            "Paramètres contractuels, ratio MOQ vs besoin réel"),
    _Racine("R016", "approvisionnement", "contrat", "Lead time réel ≠ contractuel",
            ("Mat", "Temp", "Info", "Sync"),
            "O", "O", "non", _F,
            "Dérive structurelle fournisseur, congestion amont récurrente",
            "Lead time observé glissant vs paramètre système"),
    _Racine("R017", "approvisionnement", "prevision", "Erreur de couverture",
            ("Mat", "Op", "Temp", "Info", "Sync"),
            "O", "O", "non", _F,
            "Erreur prévision demande, erreur paramétrage stock de sécurité",
            "Couverture jours, projection nette, MAPE prévision demande"),

    # ----- 3.3 Logistique (9) -----
    _Racine("R018", "logistique", "transport_entrant", "Incident transport entrant",
            ("Mat", "Op", "Temp", "Sync"),
            "N", "N", "dominant", _f,
            "Incident transporteur, congestion, douanes, météo",
            "Tracking transporteur, indicateurs OTD transport"),
    _Racine("R019", "logistique", "transport_interne", "Incident transport interne",
            ("Cap", "Op", "Temp", "Sync"),
            "O", "P", "partiel", _M,
            "Panne chariot, embouteillage flux, défaut convoyage",
            "Disponibilité chariots, taux d'utilisation, temps d'attente"),
    _Racine("R020", "logistique", "transport_sortant", "Incident transport sortant",
            ("Temp", "Sync"),
            "N", "N", "dominant", _f,
            "Incident transporteur, congestion, indisponibilité véhicule",
            "Tracking transporteur, OTD livraison client"),
    _Racine("R021", "logistique", "stockage", "Saturation capacité",
            ("Mat", "Op", "Temp", "Sync"),
            "O", "O", "non", _F,
            "Accumulation WIP, retards expédition, accumulation matières",
            "Taux d'occupation magasins, prévision flux entrants/sortants"),
    _Racine("R022", "logistique", "stockage", "Erreur de localisation",
            ("Mat", "Op", "Temp", "Info", "Sync"),
            "N", "N", "dominant", _f,
            "Erreur saisie, déplacement non tracé, vol/perte",
            "Écart inventaire tournant, alertes prélèvement"),
    _Racine("R023", "logistique", "stockage", "Péremption",
            ("Mat", "Qual"),
            "O", "O", "non", _F,
            "Rotation insuffisante, surstockage, FEFO non respecté",
            "Couverture stock, dates de péremption, FIFO/FEFO"),
    _Racine("R024", "logistique", "manutention", "Ressource indisponible",
            ("Cap", "Op", "Temp", "Sync"),
            "O", "P", "partiel", _M,
            "Absentéisme, panne, pic de charge logistique",
            "Planning RH manutention, taux d'absentéisme glissant"),
    _Racine("R025", "logistique", "manutention", "Équipement en panne",
            ("Cap", "Op", "Temp", "Sync"),
            "O", "P", "partiel", _M,
            "Défaillance technique, maintenance non planifiée",
            "Indicateurs MTBF, alertes capteurs, plan maintenance"),
    _Racine("R026", "logistique", "si_logistique", "Défaillance WMS",
            ("Mat", "Info", "Sync"),
            "N", "N", "dominant", _f,
            "Bug, panne infrastructure, désynchronisation avec ERP",
            "Logs erreurs, supervision technique, temps de réponse"),

    # ----- 3.4 Production (12) -----
    _Racine("R027", "production", "ressource_humaine", "Absentéisme",
            ("Cap", "Op", "Temp", "Sync"),
            "O", "N", "partiel", _M,
            "Maladie, événement personnel, démission",
            "Historique absentéisme glissant, période de l'année, jour de semaine"),
    _Racine("R028", "production", "ressource_humaine", "Défaut de compétence",
            ("Cap", "Op", "Qual", "Temp"),
            "O", "O", "non", _F,
            "Affectation contrainte, formation insuffisante",
            "Matrice de polyvalence, ratios qualifié/non qualifié"),
    _Racine("R029", "production", "ressource_humaine", "Polyvalence insuffisante",
            ("Cap", "Temp", "Sync"),
            "O", "O", "non", _F,
            "Recrutement insuffisant, formation longue, départs non remplacés",
            "Matrice de polyvalence, score de risque RH par poste"),
    _Racine("R030", "production", "machine", "Panne",
            ("Cap", "Op", "Temp", "Sync"),
            "O", "P", "partiel", _M,
            "Défaillance technique, usure, défaut de pièce",
            "Capteurs IoT, MTBF, alertes maintenance prédictive"),
    _Racine("R031", "production", "machine", "Maintenance non planifiée",
            ("Cap", "Op", "Temp", "Sync"),
            "O", "O", "partiel", _M,
            "Suite à panne ou alerte capteur, défaillance émergente",
            "Capteurs, supervision, plan maintenance préventive"),
    _Racine("R032", "production", "machine", "Vitesse réelle dégradée",
            ("Cap", "Qual", "Temp"),
            "O", "O", "non", _F,
            "Usure, dérive paramètres, ralentissement progressif",
            "TRS, capabilité cadence, écart cadence théorique/réelle"),
    _Racine("R033", "production", "auxiliaire", "Outillage indisponible",
            ("Cap", "Op", "Temp", "Sync"),
            "O", "P", "partiel", _M,
            "Casse, maintenance, mauvaise localisation, attente échange",
            "Disponibilité outillages, état outillages, plan changement"),
    _Racine("R034", "production", "auxiliaire", "Énergie/fluide",
            ("Cap", "Op", "Temp", "Sync"),
            "N", "N", "dominant", _f,
            "Incident réseau, défaillance équipement, pic consommation",
            "Supervision utilités, alertes opérateur énergie"),
    _Racine("R035", "production", "methode", "Gamme erronée",
            ("Op", "Qual", "Temp", "Info"),
            "O", "N", "non", _M,
            "Erreur de mise à jour, défaut process engineering",
            "Audits gamme, taux de non-conformité par gamme"),
    _Racine("R036", "production", "methode", "Paramètres dérivés",
            ("Op", "Qual"),
            "O", "O", "non", _F,
            "Dérive instrumentation, vieillissement, modification non tracée",
            "Cartes de contrôle, SPC, dérive moyenne glissante"),
    _Racine("R037", "production", "ordonnancement", "Séquencement sous-optimal",
            ("Cap", "Temp", "Sync"),
            "O", "O", "non", _F,
            "Heuristique d'ordonnancement, contraintes oubliées",
            "Indicateurs séquencement, temps de changement de série"),
    _Racine("R038", "production", "ordonnancement", "Changement de série",
            ("Cap", "Temp"),
            "O", "O", "non", _F,
            "Dérive paramètres setup, optimisation séquence insuffisante",
            "Temps SMED réel vs théorique, fréquence des changements"),

    # ----- 3.5 Qualité (8) -----
    _Racine("R039", "qualite", "non_conformite", "NC produit interne",
            ("Mat", "Cap", "Op", "Qual", "Temp", "Sync"),
            "O", "P", "partiel", _M,
            "Dérive process, défaut matière, erreur opérateur",
            "Taux NC, SPC, contrôles intermédiaires"),
    _Racine("R040", "qualite", "non_conformite", "NC fournisseur",
            ("Mat", "Op", "Qual", "Temp", "Sync"),
            "O", "O", "partiel", _M,
            "Voir racine 'Non-conformité qualité' approvisionnement",
            "PPM fournisseur, contrôles réception"),
    _Racine("R041", "qualite", "process", "Dérive paramètres",
            ("Op", "Qual"),
            "O", "O", "non", _F,
            "Usure, vieillissement, modification non tracée",
            "Cartes de contrôle, indices de capabilité, moyenne glissante"),
    _Racine("R042", "qualite", "process", "Hors contrôle statistique",
            ("Op", "Qual", "Temp", "Info"),
            "O", "P", "partiel", _M,
            "Cause spéciale, dérive brutale, incident isolé",
            "Cartes Shewhart, règles WECO, alertes SPC"),
    _Racine("R043", "qualite", "controle", "Capabilité moyen",
            ("Qual", "Info"),
            "O", "O", "non", _F,
            "R&R insuffisant, étalonnage, vieillissement moyen",
            "Études R&R, plan d'étalonnage, audits métrologie"),
    _Racine("R044", "qualite", "controle", "Fréquence inadaptée",
            ("Qual", "Temp", "Info"),
            "O", "O", "non", _F,
            "Plan de contrôle obsolète, optimisation insuffisante",
            "Ratio défauts détectés en aval / en contrôle"),
    _Racine("R045", "qualite", "retour_client", "Réclamation",
            ("Qual", "Info"),
            "N", "N", "partiel", _f,
            "Défaut non détecté, dérive non maîtrisée",
            "Taux de réclamations, historique par référence/lot/client"),
    _Racine("R046", "qualite", "retour_client", "Garantie",
            ("Qual", "Info"),
            "N", "N", "partiel", _f,
            "Défaut latent, usure prématurée, défaut de conception",
            "Taux retour garantie, MTTF terrain, historique par référence"),
)


def seed_macrs_layer1(conn: sqlite3.Connection) -> dict[str, int]:
    """Seed idempotent des 3 tables MACRS Couche 1.

    Renvoie le nombre de lignes insérées dans chaque table :
        {"categories": n, "racines": n, "incidences": n}.
    """
    inserted = {"categories": 0, "racines": 0, "incidences": 0}

    for idx, (code, label, definition) in enumerate(CATEGORIES, start=1):
        exists = conn.execute(
            "SELECT 1 FROM macrs_categories WHERE categorie_code = ?",
            (code,),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO macrs_categories "
            "(categorie_code, label, definition, ordre) "
            "VALUES (?, ?, ?, ?)",
            (code, label, definition, idx),
        )
        inserted["categories"] += 1

    for r in RACINES:
        exists = conn.execute(
            "SELECT 1 FROM macrs_racines WHERE racine_id = ?",
            (r.racine_id,),
        ).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO macrs_racines "
                "(racine_id, domaine, sous_domaine, label, "
                " predictibilite, c1_precurseur, c2_cumulative, "
                " c3_aleatoire, mecanisme, observabilite) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (r.racine_id, r.domaine, r.sous_domaine, r.label,
                 r.predictibilite, r.c1, r.c2, r.c3,
                 r.mecanisme, r.observabilite),
            )
            inserted["racines"] += 1
        for cat in r.incidences:
            exists_inc = conn.execute(
                "SELECT 1 FROM macrs_incidence "
                "WHERE racine_id = ? AND categorie_code = ?",
                (r.racine_id, cat),
            ).fetchone()
            if exists_inc:
                continue
            conn.execute(
                "INSERT INTO macrs_incidence "
                "(racine_id, categorie_code) VALUES (?, ?)",
                (r.racine_id, cat),
            )
            inserted["incidences"] += 1

    return inserted


def count_incidences(conn: sqlite3.Connection) -> int:
    """Nombre total de cellules d'incidence en base."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM macrs_incidence"
    ).fetchone()
    return int(row["n"]) if row else 0


def list_racines(
    conn: sqlite3.Connection,
    *,
    domaine: str | None = None,
    predictibilite: str | None = None,
) -> list[dict]:
    """Renvoie les racines filtrables par domaine et/ou prédictibilité."""
    sql = ("SELECT racine_id, domaine, sous_domaine, label, predictibilite "
           "FROM macrs_racines WHERE 1=1")
    params: list[object] = []
    if domaine:
        sql += " AND domaine = ?"
        params.append(domaine)
    if predictibilite:
        sql += " AND predictibilite = ?"
        params.append(predictibilite)
    sql += " ORDER BY racine_id"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def list_categories(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT categorie_code, label, definition, ordre "
        "FROM macrs_categories ORDER BY ordre"
    ).fetchall()
    return [dict(r) for r in rows]


def get_incidences_for_racine(
    conn: sqlite3.Connection, racine_id: str,
) -> list[str]:
    """Catégories Δ actives pour une racine (codes triés par ordre canonique)."""
    rows = conn.execute(
        "SELECT i.categorie_code FROM macrs_incidence i "
        "JOIN macrs_categories c ON c.categorie_code = i.categorie_code "
        "WHERE i.racine_id = ? ORDER BY c.ordre",
        (racine_id,),
    ).fetchall()
    return [r["categorie_code"] for r in rows]


def get_racines_for_category(
    conn: sqlite3.Connection, categorie_code: str,
) -> list[str]:
    rows = conn.execute(
        "SELECT racine_id FROM macrs_incidence "
        "WHERE categorie_code = ? ORDER BY racine_id",
        (categorie_code,),
    ).fetchall()
    return [r["racine_id"] for r in rows]
