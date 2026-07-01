# Document de cadrage — Système APS+MES événementiel piloté par OF

**Version** : 1.0
**Date** : 2026-07
**Statut** : Cadrage validé
**Doctrine cible** : OF+EVENT (pilotage par ordres de fabrication + event sourcing)

---

## 1. Contexte

Le projet Pilotage-flux a exploré cinq doctrines de pilotage industriel
(OF, OF+EVENT, FLUX+EVENT, OF+EVENT+BCE, FLUX+EVENT+BCE) sur horizon 60j
et 120j avec 8 puis 20 aléas de 5 types (breakdown, NC qualité,
retard PO, commande urgente, blocage logistique).

Deux études comparatives (5 seeds × 5 configurations, 25 runs par
niveau) livrent le verdict expérimental suivant :

### 1.1 Résultats stress fort (60j × 8 hazards)

| Configuration | OTIF | €/u | WIP σ | Rupture | Recovery |
|---|:-:|:-:|:-:|:-:|:-:|
| OF | 0.944 | 113.62 | 9.68 | 0.0% | 10.4j |
| **OF+EVENT** | **0.944** | **111.04** | 9.68 | **0.0%** | 10.8j |
| FLUX+EVENT | 0.916 | 113.39 | 3.03 | 1.1% | 17.4j |
| OF+EVENT+BCE | 0.944 | 111.04 | 9.68 | 0.0% | 10.8j |
| FLUX+EVENT+BCE | 0.916 | 113.31 | 3.03 | 1.1% | 17.4j |

### 1.2 Résultats stress extrême (120j × 20 hazards, 5 types)

| Configuration | OTIF | €/u | WIP σ | Rupture | Recovery |
|---|:-:|:-:|:-:|:-:|:-:|
| OF | 0.944 | 127.57 | 15.01 | 0.0% | 20.0j |
| **OF+EVENT** | **0.945** | **112.45** | 14.89 | **0.0%** | 20.0j |
| FLUX+EVENT | 0.898 | 110.85 | 2.96 | 4.4% | 18.4j |
| OF+EVENT+BCE | 0.945 | 112.45 | 14.89 | 0.0% | 20.0j |
| FLUX+EVENT+BCE | 0.898 | 110.64 | 2.96 | 4.4% | 18.4j |

### 1.3 Interprétation doctrinale

Sur objectif QCDS (Qualité, Coût, Délai, Stabilité) avec priorité au
service client (OTIF, absence de rupture) :

- **OF+EVENT domine** sur OTIF, Q, D, rupture, coût unitaire.
- **FLUX+EVENT gagne** uniquement sur stabilité WIP (σ −80%) et
  recovery (−1.6j).
- **BCE n'apporte rien de mesurable** (Δ toutes < 0.01) : les seuils
  de tolérance ne sont pas franchis même sous stress extrême.

## 2. Décision doctrinale

**La cible retenue est OF+EVENT.**

Justifications :

1. **Cible QCDS avec priorité service** : OTIF et absence de rupture
   sont les KPIs cardinaux du client industriel type. OF+EVENT est le
   seul à tenir 0% de rupture sous 20 hazards.
2. **Simplicité opérationnelle** : pas de contractualisation flux
   hebdomadaire, pas de jumeau 5 flux, pas de zone négociable enrichie
   à maintenir. Cycle de conception plus court, TCO applicatif réduit.
3. **Absence de gain BCE** : la boucle cybernétique étendue ne
   différencie pas les résultats — inutile de porter le coût de
   développement, de tests et d'exploitation associé.
4. **Rétention de l'apport EVENT** : l'event sourcing seul apporte
   −2.4% €/u et −78% nervosité vs OF pur. C'est le vrai levier.

## 3. Vision cible

Un système intégré **APS+MES à pilotage événementiel**, structuré par
ordres de fabrication (OF), couvrant le cycle complet de la demande,
organisé selon **trois zones décisionnelles** :

### 3.1 Zone libre — Prévision et engagement

Domaine de la **demande future non encore engagée**. Traitement des
demandes de délai (ATP/CTP), estimation de faisabilité, réservation
de capacité, arbitrage commercial.

- Sortie : demande **planifiée** ou **prévision** avec réservation de
  capacité horodatée.
- Réversibilité : totale (rien n'est engagé physiquement).

### 3.2 Zone négociable — Planification & ordonnancement des OFs

Domaine du **plan de production glissant**. Les OFs y sont
**planifiés**, **lissés** (takt-cohérent, DBR, capacité goulot) et
**ordonnancés** sous forme de **contrats de production**.

- Sortie : agenda goulot (drum), OFs planifiés, séquences par poste,
  contrats de production signés.
- Réversibilité : partielle (renégociation possible tant que non gelé).

### 3.3 Zone gelée — Exécution atelier (MES) + gestion événementielle

Domaine du **temps réel atelier**. Les OFs y sont **lancés**,
**exécutés** et **clôturés**. La boucle événementielle (event
sourcing) tourne en continu : expected/actual → deviations → filtre
dual tolérances → filtre dual mémoire (V13.C) → actions correctives.

- Sortie : OFs clôturés, événements réels, recettes mémoire,
  livraisons.
- Réversibilité : nulle (physique). Correction par action, non par
  replanification.

### 3.4 Cheminement d'une demande

```
Zone libre                Zone négociable              Zone gelée
──────────────────       ────────────────────         ─────────────────
Prévision                 Planification OFs            Lancement OF
Demande de délai   →      Lissage takt                 Exécution
ATP/CTP                   Ordonnancement DBR    →      Event sourcing
Réservation capa          Contrats production          Actions corrective
Confirmation SO           (signature)                  Clôture OF+SO
                                                       Recette mémoire
```

## 4. Périmètre fonctionnel

### 4.1 Périmètre inclus (par zone décisionnelle)

**Zone libre — Prévision et engagement**

| Module | État |
|---|:-:|
| Estimation ATP (stock + OFs + POs) | À développer |
| Estimation CTP (simulation faisabilité) | À développer |
| Réservation de capacité (slots WS × jour) | À développer |
| Confirmation SO + création `demand_contract` | À développer |

**Zone négociable — Planification et ordonnancement**

| Module | État |
|---|:-:|
| BOM explosion multi-niveau | Existant |
| **Nomenclature aplatie (flattened BOM)** | À développer |
| **MRP — calcul des besoins nets (pegging vers SO)** | À développer |
| **Gamme aplatie (flattened routing)** | À développer |
| **CRP — calcul des charges par WS (pegging vers SO+OF)** | À développer |
| Routing dynamique (linéaire, parallèle, hybride) | Existant |
| CPM forward/backward pass | Existant |
| Lot-sizing takt-minimal (V13.F) | Existant |
| Lissage capacity-aware (earliest-first ≤ 85%) | Existant |
| DBR bottleneck-first (V15) | Existant |
| Rope (blocage amont file goulot, V14) | Existant |
| Signature du contrat de production (`demand_contract.signed`) | À finaliser |

**Zone gelée — MES + gestion événementielle**

| Module | État |
|---|:-:|
| Lancement OF | Existant |
| Cycle opération (start/finish/scrap) | Existant |
| Event sourcing V3 (expected/actual) | Existant |
| Matching + deviations | Existant |
| Analyse causes racines | Existant |
| Filtre dual tolérances | Existant |
| Filtre dual mémoire actif (V13.C) | Existant |
| Actions correctives physiques (L5.2 + L8.1) | Existant |
| Clôture OF + capture recette P4 | Existant |
| Chaînage clôture OFs → clôture SO | À finaliser |
| Apprentissage boucle longue | Existant |
| **Plan de travail journalier opérateur** | À développer |
| **Dashboard superviseur — avancement OFs+SOs** | À développer |
| **Dashboard superviseur — événements réel vs attendu jour** | À développer |

**Transverse — Moteurs et gouvernance**

| Module | État |
|---|:-:|
| Prévision statistique linéaire + non-linéaire (ARIMA, HW, ensemble) | Existant (V12.1) |
| CPM forward/backward | Existant |
| Heuristiques (SLACK, EDD, SPT, ATC) | Existant (V12.2) |
| Moteur lissage (capacity/due-date/TOC/CPM-aware) | Existant |
| CP-SAT / OR-Tools | Existant (V12.2) |
| Sélecteur de profil (rapide / équilibré / qualité) | Existant (V12.5) |
| **MDM référentiels ERP + versioning BOM/gammes** | À développer |
| **Générateur de variantes article (axes × cartésien)** | À développer |
| **Calendriers atelier + maintenance préventive + équipes** | À développer |
| **Sous-traitance + heures supplémentaires** | À développer |
| **Simulation what-if (sandbox planificateur)** | À développer |
| **Horizon configurable (heure / jour / semaine)** | À développer |
| **RETEX cross-SO (pattern matching)** | À développer |
| **Interopérabilité ISA-95 + OPC-UA** | À développer |
| **Mode dégradé MES sans APS** | À développer |
| **Plan de travail journalier planificateur** | À développer |

### 4.2 Périmètre exclu (avec justification)

| Fonctionnalité | Raison de l'exclusion |
|---|---|
| Contractualisation flux hebdomadaire (V13.I) | Aucun apport OTIF/rupture mesuré ; complexité non justifiée |
| Jumeau numérique 5 flux persisté (V13.J) | Utile pour instrumentation FLUX, sans plus-value hors doctrine flux |
| Zone négociable enrichie (V13.K) | Utile pour FLUX ; sans doctrine flux, la zone se réduit au buffer CPM classique |
| BCE (Boucle Cybernétique Étendue) | Δ mesuré ≈ 0 sur toutes métriques ; ROI nul |
| Doctrines FLUX+EVENT / FLUX+EVENT+BCE | Perdent sur QCD (OTIF −4.7pp, rupture +4.4pp) |

## 5. Parties prenantes

| Rôle | Responsabilité principale |
|---|---|
| **Direction industrielle** | Sponsor, KPIs stratégiques (OTIF, marge) |
| **Chef d'atelier / superviseur** | Arbitrages temps réel, escalades |
| **Planificateur APS** | Ordonnancement, gestion capacité, engagement délais |
| **Opérateur atelier** | Exécution opérations, saisie MES |
| **Contrôle qualité** | Traitement NC, arbitrage rework/scrap |
| **Achats / logistique** | Gestion PO, délais composants |
| **DSI** | Intégration ERP, gouvernance données |

## 6. Enjeux et risques

### 6.1 Enjeux

- **Service client** : maintenir OTIF ≥ 0.94 sous aléas répétés.
- **Compétitivité coût** : baisser €/unité livrée de 2 à 3% vs
  pilotage OF pur (levier event sourcing).
- **Résilience** : recovery ≤ 20j sur choc majeur.
- **Traçabilité** : audit trail complet demande → OF → opérations.
- **Amélioration continue** : capitalisation par recettes mémoire.

### 6.2 Risques

| Risque | Probabilité | Impact | Mitigation |
|---|:-:|:-:|---|
| Non-adhésion opérateurs à la saisie MES | Moyen | Fort | UX simple, tablettes, saisie one-tap |
| Dérive des paramètres data-driven | Moyen | Moyen | Gouvernance seuils + revue mensuelle |
| Explosion volumétrie event store | Faible | Moyen | Rétention configurable, archivage |
| Intégration ERP défaillante | Moyen | Fort | POC intégration en phase 0 |
| Sous-utilisation filtre mémoire (V13.C) | Moyen | Faible | Formation planificateurs |

## 7. Objectifs de performance (KPIs cibles)

| KPI | Cible | Source doctrinale |
|---|:-:|---|
| **OTIF** | ≥ 0.94 | Résultat OF+EVENT stress 120j |
| **Quantity compliance** | ≥ 0.94 | Idem |
| **Disponibilité SO** | ≥ 0.99 | Idem |
| **Rupture** | 0.0% | Idem |
| **€/unité livrée** | ≤ 113 (référence stress fort) | Idem |
| **Recovery** | ≤ 20j | Idem |
| **Nervosité** | ≤ 0.02 | Idem |
| **WIP σ** | ≤ 15 (accepté vs FLUX) | Compromis assumé |

## 8. Architecture cible (macro)

Le système est structuré selon les trois zones décisionnelles (§3) qui
définissent le cheminement d'une demande de bout en bout.

```
+===============================================================+
|                       ZONE LIBRE                               |
|              (prévision, engagement délai)                     |
|                                                                |
|  Demande client / Prévision                                    |
|       ↓                                                        |
|  Estimation ATP  ──►  Estimation CTP (simulation)              |
|                            ↓                                   |
|                       Réservation capacité                     |
|                            ↓                                   |
|                       Confirmation SO                          |
|                       + demand_contract (draft)                |
+===============================================================+
                              ↓
+===============================================================+
|                     ZONE NÉGOCIABLE                            |
|         (planification, lissage, ordonnancement OFs)           |
|                                                                |
|  BOM explosion  ──►  Nomenclature aplatie (pegging → SO)       |
|                            ↓                                   |
|              MRP — Besoins nets par composant                  |
|            (net = brut − stock − PO ouverts)                   |
|                            ↓                                   |
|         Gamme aplatie (pegging OFs → SO)                       |
|                            ↓                                   |
|              CRP — Charges par workstation                     |
|         (identification goulot dynamique)                      |
|                            ↓                                   |
|  Routing dynamique  ──►  CPM forward/backward                  |
|                            ↓                                   |
|  Lissage takt-minimal  ──►  DBR bottleneck-first + Rope        |
|                            ↓                                   |
|            Contrats de production signés                       |
|            (demand_contract.signed)                            |
|            Agenda goulot (drum) publié                         |
+===============================================================+
                              ↓
+===============================================================+
|                       ZONE GELÉE                               |
|           (MES + gestion événementielle)                       |
|                                                                |
|  Lancement OF  ──►  Start/Finish op  ──►  Clôture OF          |
|         ↕                    ↕                                 |
|  ┌────────────────────────────────────────────┐               |
|  │  Boucle événementielle V3 (continue)       │               |
|  │  Expected ↔ Actual → Deviations → Causes   │               |
|  │  Filtre dual tolérances                    │               |
|  │  Filtre dual mémoire actif (V13.C)         │               |
|  │  Actions correctives physiques             │               |
|  └────────────────────────────────────────────┘               |
|                            ↓                                   |
|            Clôture SO (tous OFs closed)                        |
|            Bilan demande + capture P4                          |
+===============================================================+
```

### 8.1 Frontière zone libre / zone négociable

La bascule se déclenche à la **confirmation SO**. Tant que la SO n'est
pas confirmée (contrat en `draft`), la demande vit en zone libre : la
CTP peut être renégociée sans impact sur le plan.

### 8.2 Frontière zone négociable / zone gelée

La bascule se déclenche à la **signature du contrat de production** :
le `demand_contract.flux_doc_status` passe à `signed`. Les OFs
correspondants deviennent lançables par le MES et un
re-ordonnancement global doit être justifié.

### 8.3 Perméabilité contrôlée entre zones

- Un aléa zone gelée (breakdown, NC critique) peut, via
  `action_level = replan_global`, remonter en zone négociable.
- Une renégociation zone libre après confirmation force le retour
  en zone négociable puis re-signature.
- Aucun aller-retour direct zone libre ↔ zone gelée sans passer par
  la zone négociable.

## 9. Planning macro (à confirmer en phase suivante)

| Phase | Contenu | Durée cible |
|---|---|:-:|
| **P0 — Cadrage & POC** | Ce document + POC intégration ERP | 4-6 sem |
| **P1 — Prévision & devis** | ATP/CTP + réservation capacité + confirmation SO | 8-10 sem |
| **P2 — Clôture demande** | Chaînage OFs → SO closure | 3-4 sem |
| **P3 — Consolidation MES** | UX opérateur, tablettes, saisie temps réel | 6-8 sem |
| **P4 — Gouvernance data-driven** | Revue paramètres, dashboards, formation | 4 sem |
| **P5 — Bilan & industrialisation** | Recette, transfert, run | 4 sem |

**Total indicatif** : 29 à 36 semaines.

## 10. Livrables du cadrage

1. **Ce document** (`cadrage_of_event.md`).
2. **Cahier des charges détaillé** (`cahier_des_charges_of_event.md`).
3. **User stories** (`user_stories_of_event.md`).
4. **Rapports d'étude comparative** :
   - `of_flux_event_bce_report.md` (stress fort)
   - `of_flux_event_bce_extreme_report.md` (stress extrême)
5. **Décision d'exclusion FLUX/BCE** — actée par ce document, §2.

## 11. Normes et interopérabilité (positionnement)

Le système cible se positionne dans le cadre normatif industriel de
référence pour garantir crédibilité et intégration au SI :

- **ISA-95** : hiérarchie fonctionnelle Level 3 (MES) / Level 4 (APS).
  Échanges normalisés B2MML entre niveaux.
- **OPC-UA** : intégration temps réel avec SCADA, automates et machines
  connectées. Le MES expose un serveur OPC-UA.
- **ISO 9001** : traçabilité audit et gouvernance qualité.
- **ISO 27001** : sécurité de l'information.

Le respect de ces normes n'est pas un objectif V1 en tant que tel
mais oriente les choix d'architecture (API, event schemas, hiérarchie
des rôles).

## 12. Roadmap V2 (au-delà du présent cadrage)

Les fonctionnalités suivantes sont **identifiées comme cibles V2**,
à réévaluer selon retour d'exploitation de la V1 :

- **Multi-site avec transferts inter-sites** : plusieurs sites
  physiques, MRP+CRP consolidés multi-site, allocation SO → site
  optimisée.
- **Retour d'expérience (RETEX) enrichi** : pattern matching cross-SO
  avancé, suggestions d'ajustement paramétrique automatiques,
  dashboard interactif.
- **Horizon horaire pour cycles courts** : granularité heure pour
  secteurs à cycle court (agroalimentaire, pharma).
- **Intégration ERP native** au-delà de l'import batch : CDC + API
  bidirectionnelle temps réel.
- **Configurateur client** : outil externalisé permettant au client
  final de spécifier ses variantes → génération SKU + estimation
  délai en ligne.

## 13. Validation

Ce cadrage est validé sur la base des résultats expérimentaux joints.
Toute demande d'inclusion ultérieure de la couche FLUX ou BCE devra
être justifiée par un cas d'usage où :

- soit **la stabilité WIP** est un KPI cardinal (industrie process,
  chimie fine, semi-conducteurs),
- soit **des seuils BCE resserrés** permettent de démontrer un gain
  mesurable sur OTIF/coût.

En absence de tel cas, la doctrine cible reste **OF+EVENT**.
