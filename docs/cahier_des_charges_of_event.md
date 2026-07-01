# Cahier des charges — Système APS+MES événementiel piloté par OF

**Version** : 1.0
**Date** : 2026-07
**Doctrine** : OF+EVENT
**Référence cadrage** : `cadrage_of_event.md`

---

## 1. Objet

Le présent cahier des charges spécifie le système intégré APS+MES à
pilotage événementiel, structuré autour des ordres de fabrication (OF)
et couvrant le cycle complet : prévision → devis → réservation
capacité → confirmation commande → planification → exécution atelier →
clôture OFs → clôture demande.

L'architecture ne comporte pas de couche flux (contrat hebdomadaire,
jumeau 5 flux, zone négociable enrichie) ni de boucle cybernétique
étendue (BCE), leur apport n'ayant pas été démontré sur objectif QCDS
(cf. cadrage §1).

## 2. Exigences fonctionnelles

Les exigences sont regroupées selon les **trois zones décisionnelles**
définies dans le cadrage (§3) :

- **Zone libre** (§2.1) : prévision, ATP/CTP, réservation, confirmation.
- **Zone négociable** (§2.2 à §2.4) : planification, lissage,
  ordonnancement, signature contrat de production.
- **Zone gelée** (§2.5 à §2.8) : MES, event sourcing, filtres duals,
  clôture.

---

## ZONE LIBRE — Prévision et engagement

### 2.1 Prévision et devis (module APS-Estimate)

#### EF-01 — Estimation ATP (Available To Promise)

Le système doit, pour toute demande client entrante (article, quantité,
date souhaitée), calculer une date de disponibilité au plus tôt en
tenant compte :

- du stock disponible non alloué,
- des OFs en cours dont le fini est le même article,
- des OFs planifiés livrant l'article dans l'horizon,
- des PO composants ouverts.

**Sortie** : `promised_date`, `available_qty_at_date`, `confidence_score`.

#### EF-02 — Estimation CTP (Capable To Promise)

Si l'ATP est insuffisant, le système doit calculer une date de faisabilité
industrielle en simulant :

- explosion BOM,
- routing optimal (linéaire / parallèle / hybride),
- ordonnancement sur capacité disponible avec identification du goulot,
- prise en compte des délais composants.

**Sortie** : `feasibility_date`, `bottleneck_ws`, `takt_target_min`,
`wip_predicted`, `feasible: bool`.

#### EF-03 — Réservation de capacité

Sur validation planificateur d'une CTP, le système doit réserver des
slots de capacité (workstation, jour, minutes) pour la durée de
production estimée.

**Contraintes** :

- La réservation bloque la capacité vis-à-vis d'autres estimations CTP.
- Une réservation non confirmée en X jours (paramétrable, défaut 7) est
  libérée automatiquement.
- La réservation est convertie en OFs planifiés à la confirmation SO.

### 2.2 Confirmation de commande (bascule zone libre → zone négociable)

#### EF-04 — Création SO (Sales Order)

Sur confirmation client, le système doit :

- créer un `sales_order` avec `promised_date`, `quantity`, `article_id`,
- lier la réservation de capacité existante,
- créer un `demand_contract` (contrat de demande) portant les cibles
  QCDS (takt, WIP, rho goulot, buffer).

#### EF-05 — Contrat de demande

Chaque SO génère un contrat de demande contenant :

| Champ | Description |
|---|---|
| `so_id` | Référence commande |
| `bottleneck_ws` | Goulot identifié |
| `takt_target_min` | Cadence cible sur goulot |
| `wip_target` | WIP prévisionnel (Little) |
| `rho_bottleneck` | Taux d'occupation goulot |
| `buffer_days` | Marge CPM |
| `feasible` | Faisabilité industrielle |
| `flux_doc_status` | signed / draft |

Un contrat non signé peut être renégocié (délai, quantité).

---

## ZONE NÉGOCIABLE — Planification et ordonnancement des OFs

### 2.3.0 MRP — Calcul des besoins nets sur nomenclature aplatie

#### EF-05a — Nomenclature aplatie (flattened BOM)

Le système doit calculer, pour chaque article fini demandé, la
nomenclature **aplatie** consolidant tous les niveaux de la BOM
multi-niveau en un seul tableau (composant → coefficient cumulé).

**Sortie :** table `flattened_bom` :

| Champ | Description |
|---|---|
| `finished_article_id` | Article fini demandé |
| `component_article_id` | Composant terminal (semi ou acheté) |
| `qty_per_finished_unit` | Coefficient consolidé (multiplication en chaîne) |
| `min_level_in_bom` | Niveau le plus haut où le composant apparaît |
| `is_purchased` | Vrai si composant acheté (déclenchera PO) |

#### EF-05b — Calcul des besoins nets (MRP)

Pour chaque composant identifié via la nomenclature aplitie et sur
horizon glissant :

```
besoin_brut(c, d)  = Σ (qty_per_finished × qty_demandée) pour SO due(d)
stock_disponible(c, d) = stock_initial(c)
                       + Σ PO_reçues(c, d' ≤ d)
                       − Σ consommations_engagées(c, d' ≤ d)
besoin_net(c, d)   = max(0, besoin_brut(c, d) − stock_disponible(c, d))
```

**Sortie :** table `mrp_net_requirements` avec un enregistrement par
(composant, jour, SO source).

#### EF-05c — Pegging BOM (bottom-up)

Chaque ligne de besoin net doit être **peggée** vers la ou les SO
sources qui l'ont générée. Le pegging établit une traçabilité
bidirectionnelle :

```
SO ──BOM aplatie──► besoin brut ──MRP──► besoin net
SO ◄─pegging────── besoin net (traçabilité)
```

**Contraintes :**

- Un besoin net partagé entre plusieurs SOs doit être **ventilé** au
  prorata des demandes (règle configurable : `pro_rata` par défaut,
  `earliest_first` en option).
- Chaque `mrp_net_requirements` ligne porte une colonne `pegging_json`
  listant les `(sales_order_id, qty_attributed, due_date)`.
- Modification d'une SO doit propager le recalcul et re-pegging
  automatique.

#### EF-05d — Déclenchement PO / OF depuis MRP

- Si `component.is_purchased = 1` et `besoin_net > 0` : le système
  suggère un `purchase_order` avec quantité = besoin_net + lot_size
  standard, date livraison = due_date − lead_time.
- Si composant fabriqué : le système suggère un OF avec quantité
  = besoin_net (respectant lot mini article).

### 2.3.1 CRP — Calcul des charges sur gamme aplatie

#### EF-05e — Gamme aplatie (flattened routing)

Pour chaque article fini, le système doit calculer la gamme
**aplatie** consolidant les gammes de tous les composants fabriqués
de la BOM (temps machine cumulés par workstation).

**Sortie :** table `flattened_routing` :

| Champ | Description |
|---|---|
| `finished_article_id` | Article fini |
| `workstation_id` | Poste concerné |
| `unit_time_min` | Temps total (min) par unité finie sur ce poste |
| `operations_count` | Nb d'opérations agrégées |
| `sequence_min_idx` | Séquence la plus amont |

#### EF-05f — Calcul des charges (CRP)

Pour chaque workstation et chaque jour de l'horizon :

```
charge_brute(ws, d) = Σ (unit_time_min × qty_planifiée) pour OFs
                     touchant ws et prévus pour d
capacite(ws, d)      = daily_minutes × capacity_factor(ws)
taux_charge(ws, d)   = charge_brute / capacite
```

**Sortie :** table `crp_workstation_load` avec `(workstation_id, day,
charge_min, capacite_min, taux_charge, is_bottleneck)`.

Le workstation avec `taux_charge` maximal sur l'horizon est marqué
`is_bottleneck = 1` (goulot dynamique).

#### EF-05g — Pegging routing (top-down)

Chaque ligne CRP doit être **peggée** vers les OFs qui la génèrent,
eux-mêmes peggés vers les SOs. Le pegging routing établit la chaîne :

```
SO ──► OFs planifiés ──► charge par WS (CRP) ──pegging──► SO
```

**Contraintes :**

- Chaque `crp_workstation_load` ligne porte `pegging_ofs_json` listant
  les OFs contribuant, chaque OF portant lui-même son `sales_order_id`.
- Une renégociation SO doit permettre d'identifier tous les WS
  impactés et le delta de charge.

#### EF-05h — Alerte surcharge

Si `taux_charge(ws, d) > 1.0` pour au moins un jour de l'horizon
signé :

- Notification planificateur.
- Suggestion : décaler des OFs peggés à faible priorité, ou augmenter
  capacité (heures sup, sous-traitance).

### 2.3.2 Planification APS (décomposition + routing)

#### EF-06 — Décomposition BOM

Le système doit exploser la nomenclature multi-niveau pour générer
tous les OFs nécessaires (fini, semi-finis, composants achetés
identifiés en PO).

#### EF-07 — Routing dynamique

Pour chaque OF, le système sélectionne le routing (linéaire, parallèle,
hybride) en fonction de la charge des workstations candidates.

#### EF-08 — Ordonnancement DBR (Drum-Buffer-Rope)

Le système ordonnance en deux phases :

- **Phase Bottleneck-first** : place les opérations goulot en priorité
  selon EDD (Earliest Due Date), respectant `rho ≤ 0.85` par défaut.
- **Phase Rope** : blocage amont si la file goulot dépasse le seuil
  configuré (`bottleneck_queue_max`).

**CPM** : forward/backward pass fournit ES/LS, buffer, slack.

#### EF-09 — Lot-sizing takt-minimal

Le système minimise le nombre d'OFs par SO en agrégeant la demande
sur des lots takt-cohérents. Un split n'est réalisé que si le lot
dépasse la capacité goulot journalière (`bottleneck_daily_capacity_max`).

### 2.4 Signature du contrat de production (bascule zone négociable → zone gelée)

#### EF-09b — Signature du contrat de production

Le passage `demand_contract.flux_doc_status` de `draft` à `signed`
transforme les OFs planifiés en OFs **lançables** par le MES.

**Contraintes :**

- Signature bloquée si `feasible = 0` sans dérogation planificateur.
- Signature déclenche publication de l'agenda goulot (drum).
- Un OF signé ne peut plus être supprimé, seulement replanifié via
  boucle événementielle (`replan_global`).

---

## ZONE GELÉE — MES + gestion événementielle

### 2.5 MES (exécution atelier)

#### EF-10 — Lancement OF

Le système lance un OF à sa `launch_day` planifiée. Un OF non-lançable
(composant manquant) déclenche un événement `deviation` traité par la
boucle événementielle.

#### EF-11 — Cycle opération

Chaque opération d'un OF suit le cycle :

```
scheduled → started → finished (qty_good, qty_scrap)
```

Le MES doit :

- vérifier les prérequis (composants disponibles, poste libre),
- horodater start/finish,
- calculer le rendement par workstation,
- alimenter les événements réels (`actual_events`).

#### EF-12 — Clôture OF

Un OF est clôturé quand toutes ses opérations sont finished. La
clôture déclenche :

- capture recette mémoire P4 (V13.C).
- décrément stock composants effectivement consommés.
- incrément stock article produit.
- mise à jour du contrat de demande lié (avancement).

### 2.6 Event Sourcing

#### EF-13 — Génération Expected

À la confirmation d'un batch d'OFs planifiés, le système génère les
`expected_events` (départ opération, fin opération, quantité attendue,
horodatage attendu).

#### EF-14 — Ingestion Actual

Chaque événement MES (start, finish, scrap, breakdown, NC) est
enregistré comme `actual_event`.

#### EF-15 — Matching + Deviation

Un moteur de matching apparie chaque actual à un expected. L'écart
(temps, quantité, apparition/disparition) génère un `event_deviation`
avec les champs :

| Champ | Description |
|---|---|
| `deviation_kind` | time / qty / missing / unexpected |
| `delta_time` / `delta_qty` | Écart mesuré |
| `score` | Magnitude normalisée [0..1] |
| `is_absorbed` | Absorbée par CPM (marge slack) |

#### EF-16 — Analyse causes

Pour chaque déviation non absorbée, le moteur attache les causes
racines candidates (rule-based : R-RC-XX) avec score de vraisemblance.

#### EF-17 — Filtre dual de tolérances

Le système évalue chaque déviation via un filtre dual :

```
score_combined = magnitude × (1 + log(1 + frequency_in_window))
action_level = mapping(score_combined, thresholds)
```

Niveaux d'action :

| Niveau | Signification |
|---|---|
| `inform` | Journalisation uniquement |
| `watch` | Surveillance renforcée |
| `correct_local` | Action corrective locale automatique |
| `replan_local` | Replanification locale du poste |
| `escalate` | Escalade humaine (chef d'atelier) |
| `replan_global` | Re-ordonnancement global (planificateur) |

Seuils configurables via table `parameters` (data-driven).

#### EF-18 — Filtre dual de mémoire (V13.C)

À la clôture P4, le système capture la « recette » :

```
signature = (deviation_kind, cause_rule_id, action_level)
score_combined = (significance + recurrence) / 2
```

Si `score_combined ≥ memory_learning_threshold` (défaut 0.5), la
recette est retenue.

**Actif (skip latency)** : si le flag
`enable_dual_memory_skip_latency` = 1 et qu'une action_level a été
retenue ≥ `memory_shortcut_min_recurrence` fois (défaut 2) pour un
`deviation_kind` donné, le filtre dual de tolérances est court-circuité
et l'action est appliquée immédiatement (`source='memory_shortcut'`,
`latency=0`).

#### EF-19 — Actions correctives physiques

Sur `action_level ∈ {correct_local, replan_local}`, le système
déclenche automatiquement :

- ajustement paramètre poste (vitesse, tolérance qualité),
- redispatch charge sur poste alternatif,
- création OF de rework si NC critique.

Sur `escalate` / `replan_global`, notification humaine.

### 2.7 Apprentissage boucle longue

#### EF-20 — Mise à jour paramètres data-driven

Les paramètres suivants doivent pouvoir être mis à jour depuis une
recette retenue (via `update_parameter_from_learning`) :

- seuils tolérance (`tolerance_threshold_*`)
- fenêtre fréquence (`tolerance_window_hours`)
- latence (`tolerance_latency_minutes`)
- marge CPM (`cpm_margin_minutes`)
- taux d'occupation cible (`toc_target_saturation`)

Chaque update est tracé dans `memory_filter_decisions`
(decision='update_rule', old_value, new_value).

### 2.8 Clôture de la demande

#### EF-21 — Chaînage clôture OF → SO

Quand tous les OFs liés à un SO sont clôturés et que la quantité
cumulée livrée ≥ quantité commandée, le SO passe en statut `closed`.

#### EF-22 — Livraison

Le système enregistre la livraison effective (date, quantité) et
calcule les KPIs de service (OTIF, retard, quantity_compliance).

#### EF-23 — Clôture demande

À la clôture SO, le contrat de demande passe en statut `fulfilled` et
un rapport de bilan est généré :

- écart plan / réel (temps, coût, qualité),
- recettes mémoire capturées durant le cycle,
- suggestions d'ajustement paramètres.

## 3. Exigences non-fonctionnelles

### 3.1 Performance

| Exigence | Cible |
|---|:-:|
| Temps de réponse ATP | ≤ 500 ms |
| Temps de réponse CTP (simulation) | ≤ 3 s |
| Débit événements MES | ≥ 100 evt/s |
| Latence traitement déviation (P0 → décision) | ≤ 2 s |
| Ré-ordonnancement global | ≤ 30 s (100 OFs) |

### 3.2 Disponibilité

- MES : 99.5% (heures ouvrées).
- APS : 99% (24/7).
- Event store : réplication asynchrone.

### 3.3 Volumétrie cible

| Objet | Volume horizon 1 an |
|---|:-:|
| SOs | 10 000 |
| OFs | 100 000 |
| Opérations | 500 000 |
| Événements réels | 5 000 000 |
| Déviations | 50 000 |
| Recettes mémoire | 5 000 |

### 3.4 Sécurité

- Authentification SSO (OAuth2 / OIDC).
- Autorisation basée rôles (planificateur, superviseur, opérateur,
  chef d'atelier, direction).
- Audit log immuable de toutes décisions APS/MES et actions humaines.
- Chiffrement au repos et en transit.

### 3.5 Traçabilité

- Chaque OF traçable de la SO au produit livré.
- Chaque décision APS traçable (règle appliquée, paramètres actifs).
- Chaque action corrective traçable (déviation source, source
  décision : tolérance vs mémoire).

### 3.6 Intégration

| Système | Sens | Contenu |
|---|:-:|---|
| ERP | ← | SOs, POs, stocks, articles, BOM |
| ERP | → | Livraisons, mouvements stock |
| GMAO | ← | Pannes machines, disponibilité |
| GMAO | → | Alertes anomalies |
| Qualité (LIMS) | ← | Résultats contrôles, NC |
| MES terrain (tablettes) | ↔ | Saisie opérateurs, écrans postes |

## 4. Contraintes techniques

### 4.1 Stack technique cible (indicatif)

- **APS+MES** : Python 3.11+ (base actuelle Pilotage-flux).
- **BDD** : PostgreSQL (production), SQLite (dev/tests).
- **Event store** : PostgreSQL avec table dédiée + archivage S3.
- **API** : REST + WebSocket pour événements temps réel.
- **UI opérateur** : PWA responsive, offline-first.
- **UI planificateur** : Web SPA (React ou équivalent).
- **CI/CD** : tests unitaires + acceptation obligatoires (941+ tests
  actuels à porter).

### 4.2 Doctrine de développement

- **Data-driven** : tous les seuils via table `parameters`, jamais
  codés en dur.
- **Event sourcing pur** : les événements sont la source de vérité,
  les états sont projetés.
- **Idempotence** : toute opération APS/MES doit être ré-exécutable
  sans effet de bord.
- **Test coverage** : ≥ 80% sur modules cœur.

## 5. Livrables attendus

| Livrable | Format | Phase |
|---|---|:-:|
| POC intégration ERP (ATP + création SO) | Code + tests | P0 |
| Module estimation ATP/CTP | Code + tests + doc | P1 |
| Réservation de capacité | Code + tests + doc | P1 |
| UI planificateur (devis + confirmation) | SPA + tests E2E | P1 |
| Chaînage OF → SO closure | Code + tests + doc | P2 |
| Rapport bilan demande | Template + générateur | P2 |
| UI opérateur MES (tablette) | PWA + tests E2E | P3 |
| Dashboard KPIs (OTIF, rupture, WIP) | Web + tests | P3 |
| Documentation utilisateur par rôle | Markdown / PDF | P4 |
| Documentation d'exploitation | Markdown | P4 |
| Plan de recette | Markdown | P5 |

## 6. Recette

### 6.1 Critères d'acceptation globale

Le système est acceptable si, sur un horizon de simulation de 60j
avec 8 aléas et 5 seeds, il maintient :

| KPI | Seuil |
|---|:-:|
| OTIF | ≥ 0.94 |
| Rupture | 0.0% |
| Recovery | ≤ 12j |
| €/unité | ≤ baseline OF −2% |
| Nervosité | ≤ 0.05 |

### 6.2 Critères par module

Chaque module doit passer ses tests unitaires + tests d'acceptation
(voir user stories associées, `user_stories_of_event.md`).

## 7. Hors périmètre

Sont explicitement hors périmètre de ce cahier des charges :

- Contractualisation flux hebdomadaire (V13.I).
- Jumeau numérique 5 flux persisté (V13.J).
- Zone négociable enrichie (V13.K).
- Boucle cybernétique étendue (BCE).
- Prévision statistique long-terme (ML, séries temporelles) — traitée
  par le module Forecasting V12.1 existant, non repris dans la cible.
- Fonctions de mise en marché (CRM, e-commerce).
- Gestion financière (comptabilité, facturation).

## 8. Références

- `cadrage_of_event.md` — décision doctrinale et périmètre.
- `user_stories_of_event.md` — stories par rôle.
- `of_flux_event_bce_report.md` — étude stress fort.
- `of_flux_event_bce_extreme_report.md` — étude stress extrême.
- `cadrage_v4.md` — cadrage doctrinal complet (référence académique).
- `paper_hal_v1.md` — article de recherche associé.
