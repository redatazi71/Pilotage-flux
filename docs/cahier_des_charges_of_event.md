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

#### EF-16 — Matrice d'analyse des causes racines

Le système entretient une **matrice de règles de causes racines**
(rule-based) associant chaque `deviation_kind` à un ensemble de causes
candidates, avec score de vraisemblance.

**Modèle de données :**

| Objet | Description |
|---|---|
| `cause_rules` | Table des règles R-RC-XX (id, kind cible, expression prédicat, score de base, active) |
| `event_deviation_causes` | Liaison déviation ↔ cause avec score final calculé |
| `top_causes_across_deviations` | Agrégat matriciel pour le RETEX (cf. §2.15) |

**Comportement :**

- Pour chaque déviation non absorbée, `attach_causes_to_deviation`
  parcourt la matrice et attache toutes les causes actives dont le
  prédicat s'évalue à vrai.
- Le score final tient compte du contexte (heure, workstation,
  historique) via le prédicat de la règle.
- La matrice est **paramétrable** (ajout / désactivation de règles
  R-RC-XX sans redéploiement).
- Le RETEX (§2.15) exploite `top_causes_across_deviations` pour
  identifier les patterns récurrents.

**Note d'implémentation** : `apply_cpm_absorption` (module
`events_v3/cpm.py`) est défini mais **n'est actuellement pas invoqué
dans le runner** ; le champ `event_deviations.is_absorbed` reste
donc systématiquement à `false`. Cette pré-filtre CPM (absorption
des petits deltas dans la marge de slack) est à re-wire dans le
runner pour être fonctionnellement actif ; c'est un point technique
à corriger dans la phase déploiement.

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

---

## TRANSVERSE — Moteurs, données de référence, exploitation

### 2.9 Moteurs algorithmiques

Le système intègre plusieurs moteurs sélectionnés dynamiquement selon
le contexte (SO à forte valeur, urgence, taille horizon, temps budget).

#### EF-24 — Moteur de prévision statistique (zone libre)

Alimente l'ATP/CTP par des prévisions sur la demande latente et les
délais fournisseurs.

- **Linéaires** : régression linéaire, tendance (moyenne mobile,
  régression polynomiale ordre 2).
- **Non-linéaires / séries temporelles** : ARIMA, Holt-Winters.
- **Ensemble** : combinaison pondérée par erreur historique
  (bias correction, hazard-aware).
- **Sortie** : prévision + intervalle de confiance + erreur MAPE.

#### EF-25 — Moteur CPM

Calcule ES/LS/slack/marge sur les OFs planifiés (existant, à
conserver).

#### EF-26 — Moteur heuristique (zone négociable)

Règles rapides pour re-planification locale ou fallback CP-SAT
timeout :

- SLACK, EDD (Earliest Due Date), SPT (Shortest Processing Time),
  ATC (Apparent Tardiness Cost).
- Configurables via table `heuristic_rules`.

#### EF-27 — Moteur de lissage (zone négociable)

Étale la charge sur horizon glissant avec 3 variantes complémentaires :

- **Capacity-aware** : earliest-first à `rho ≤ 0.85`.
- **Due-date aware** : prend en compte les échéances SO.
- **TOC-aware** : DBR (Drum-Buffer-Rope), goulot dynamique.
- **CPM-based** : utilise SLACK pour arbitrer priorité.

#### EF-28 — Moteur CP-SAT (OR-Tools)

Optimisation ordonnancement zone négociable pour SOs à forte valeur.

- Contraintes : capacité goulot, dépendances CPM, tolérance échéances.
- Time budget paramétrable (défaut 60s).
- Fallback heuristique si timeout.

#### EF-29 — Sélecteur de profil algorithmique

Sélectionne dynamiquement le moteur d'ordonnancement selon 3 profils :

| Profil | Cas d'usage | Moteur principal |
|---|---|---|
| `fast` | Re-planification temps réel, ateliers < 20 OFs | Heuristique EDD |
| `balanced` | Planification quotidienne standard | Lissage TOC + CPM |
| `quality` | Planification hebdomadaire, SOs stratégiques | CP-SAT + fallback |

Configuration via table `algorithm_profiles`, matrice de sélection
`algorithm_matrix` par (taille, valeur, urgence).

### 2.10 Données de référence (MDM)

#### EF-30 — Import référentiels ERP

Le système doit importer depuis l'ERP :

- Articles (`article_id`, `label`, `is_purchased`, `lot_min`).
- BOM multi-niveau (`article_parent`, `article_child`, `qty_per`).
- Routings (`article_id`, `sequence_idx`, `workstation_id`,
  `unit_time_min`).
- Workstations et capacités.
- Coûts standards (matière, main d'œuvre, machine).

**Import** : batch nightly + delta CDC (Change Data Capture) sur
événements ERP.

#### EF-31 — Versioning BOM et routings

Chaque modification BOM ou routing crée une nouvelle version.

- `bom_version` + `valid_from` / `valid_to`.
- OFs en cours restent sur la version au moment de leur création.
- Nouveaux OFs prennent la version active.
- Historique consultable et rollback autorisé (planificateur senior).

#### EF-32 — Publication contrôlée

Les modifications MDM ne prennent effet qu'après :

- Validation par référent MDM.
- Test simulation impact plan actuel.
- Publication versionnée + notification équipes.

#### EF-33 — Coûts standards

- Coût matière par article (mise à jour trimestrielle).
- Coût main d'œuvre par workstation (mise à jour semestrielle).
- Coût machine (amortissement + énergie) par workstation.
- Utilisé par KPI €/unité livrée.

### 2.11 Calendriers et exploitation atelier

#### EF-34 — Calendriers atelier

Le système gère plusieurs calendriers :

- `daily_minutes` par jour ouvré et workstation.
- Jours fériés, ponts, fermetures collectives.
- Équipes multiples (2×8, 3×8, week-end) avec `shift_id`,
  `start_time`, `end_time`.

Impacte directement CRP (§2.3.1) et CPM (§2.3.2).

#### EF-35 — Maintenance préventive (GMAO)

- Créneaux de maintenance planifiée par workstation.
- Réduction automatique de la capacité disponible.
- Alerte planificateur si maintenance imminente sur goulot.
- Intégration GMAO (import ou webhook).

#### EF-36 — Changements d'équipe

- Configuration cycle équipes (2×8, 3×8, jour seul).
- Prise en compte automatique dans CRP.
- Ajustement heures ouvertes du dashboard opérateur (US-11).

### 2.12 Flexibilité capacité

#### EF-37 — Sous-traitance

Le système doit permettre de déclarer des opérations sous-traitées :

- Article + opération → prestataire externe.
- Attributs : `subcontractor_id`, `lead_time_days`, `unit_cost_eur`,
  `capacity_max_per_day`.
- Peggée comme une workstation virtuelle dans CRP.
- Déclenche PO service + suivi livraison via boucle événementielle.

#### EF-38 — Heures supplémentaires

- Créneaux heures sup activables jour par jour, workstation par
  workstation.
- Marge de dépassement `daily_minutes_max` configurable
  (défaut +20%).
- Coût majoré appliqué au KPI €/unité.
- Autorisation requise (chef d'atelier).

### 2.13 Simulation what-if (sandbox planificateur)

#### EF-39 — Sandbox de simulation

Le planificateur doit pouvoir simuler l'impact d'une modification
avant de l'engager :

- Ajout SO fictive → prévision impact OTIF + charge.
- Injection aléa (panne, retard PO) → prévision recovery.
- Changement paramètre (`toc_target_saturation`, `cpm_margin_minutes`)
  → comparaison KPIs.
- Sandbox isolée (base éphémère, pas d'impact production).
- Diff clair : plan actuel vs plan simulé, KPIs, alertes.

### 2.14 Horizon modifiable

#### EF-40 — Configuration granularité horizon

Le système doit supporter plusieurs granularités d'horizon selon
métier :

| Granularité | Cas d'usage | Horizon typique |
|---|---|:-:|
| Horaire | Agroalimentaire, pharma cycle court | 24-48h |
| Journalière | Manufacturing standard (défaut) | 30-90j |
| Hebdomadaire | Assemblage lourd, projets long cycle | 6-12 mois |

Configuration via paramètre `planning_granularity`. Tous les calculs
(MRP, CRP, CPM, lissage) s'adaptent automatiquement.

### 2.15 Retour d'expérience (RETEX) cross-SO

#### EF-41 — Rapport RETEX pattern matching

Au-delà du bilan SO individuel (EF-23), le système produit un rapport
RETEX cross-SO périodique :

- Identification des patterns récurrents d'échec (top signatures
  déviations non retenues comme succès).
- Identification des patterns récurrents de succès (recettes fréquentes
  retenues avec outcome success).
- Corrélation aléa → dérive KPI.
- Suggestion de mise à jour paramètres data-driven (feed vers §2.7).

**Livraison** : rapport mensuel + dashboard interactif.

### 2.16 Plans de travail journaliers et dashboards superviseur

Le système doit produire, pour chaque utilisateur en début de journée
(ou sur demande), un plan de travail personnalisé et, pour chaque
superviseur, deux dashboards temps réel.

#### EF-46 — Plan de travail journalier opérateur

Pour chaque opérateur (P4), le système génère un plan basé sur les
`expected_events` de la journée sur les workstations qui lui sont
assignées.

**Contenu :**

| Champ | Description |
|---|---|
| `expected_time` | Heure prévue de l'opération |
| `of_id` + `article_id` | OF concerné + article produit |
| `sequence_idx` | Étape sur l'OF |
| `qty_expected` | Quantité prévue |
| `unit_time_min` | Temps standard |
| `status` | scheduled / started / finished / blocked |
| `prerequisites_ok` | Composants + poste disponibles |

**Comportement :**

- Rafraîchissement automatique (WebSocket / polling ≤ 30 s).
- Priorisation visuelle (couleurs par urgence, marqueur goulot).
- Actions directes : start / finish depuis la ligne.
- Export PDF / impression papier (mode off-line).

#### EF-47 — Plan de travail journalier planificateur

Pour chaque planificateur (P2), synthèse des actions attendues sur la
journée :

- SOs à confirmer (issues de la zone libre, CTP validées).
- Contrats de production à signer (bascule zone négociable → gelée).
- Alertes surcharge CRP à traiter.
- Suggestions PO ou OFs à valider (issues du MRP).
- Simulations what-if en cours à finaliser.
- Suggestions RETEX à examiner.

**Comportement :** liste triée par priorité (urgence, valeur SO), lien
direct vers l'action.

#### EF-48 — Dashboard superviseur — Avancement OFs et SOs

Pour chaque superviseur / chef d'atelier (P3), une vue temps réel de
l'avancement production :

**Bloc OFs :**

- Liste des OFs en cours et planifiés du jour.
- Colonnes : of_id, article, quantité, WS courante, sequence courante /
  total, statut, avancement %, ETA fin, SO liée.
- Filtres : par workstation, par statut, par famille produit.
- Alertes visuelles : retard, blocage prérequis, écart qualité.

**Bloc SOs :**

- Liste des SOs actives (signées non clôturées).
- Colonnes : sales_order_id, article, quantité, quantité livrée %,
  due_date, écart prévu (jours), nb OFs liés (en cours / total).
- Regroupement possible par client, famille.

**Bloc KPIs jour :**

- OTIF instantané (ratio livraisons jour / prévues jour).
- Rho goulot courant vs cible.
- WIP en cours vs plan.
- Nb déviations ouvertes.

#### EF-49 — Dashboard superviseur — Événements réels vs attendus

Vue détaillée temps réel des écarts observés dans la journée :

**Bloc événements attendus vs réels :**

Tableau avec 1 ligne par `expected_event` de la journée :

| Colonne | Description |
|---|---|
| `expected_time` | Heure prévue |
| `actual_time` | Heure réelle (vide si non produit) |
| `of_id` + `operation` | OF + opération |
| `qty_expected` | Quantité prévue |
| `qty_actual` | Quantité observée |
| `delta_time_min` | Écart temporel (min) |
| `delta_qty` | Écart quantité |
| `deviation_kind` | Nature écart (time / qty / missing / unexpected) |
| `is_absorbed` | Absorbé CPM |
| `action_level` | inform / watch / correct_local / ... |
| `source_decision` | tolerance / memory_shortcut |

**Filtres :** par workstation, par sévérité (action_level), par SO
peggée, par plage horaire.

**Regroupements agrégés :**

- Nb déviations par kind sur la journée.
- Top 5 workstations en dérive.
- Distribution action_level.
- Ratio `memory_shortcut` / `tolerance` (efficacité V13.C).

**Actions directes :**

- Ouvrir le détail d'une déviation → causes racines + décision
  courante.
- Surcharger une action recommandée (US-19).
- Marquer une déviation comme « traitée » manuellement.

### 2.17 Générateur de variantes d'articles

#### EF-42 — Axes de variantes

Le système doit permettre de définir un **article configurable** avec
plusieurs axes d'attributs (ex : taille × couleur × finition).

**Modèle :**

| Objet | Description |
|---|---|
| `article_template` | Article générique (ex : « pull ») |
| `variant_axis` | Axe de variation (ex : taille, couleur) |
| `variant_value` | Valeur possible sur un axe (ex : M, L / rouge, noir) |

#### EF-43 — Génération de SKUs par produit cartésien

À partir d'un `article_template` et de ses axes, le système génère les
`article_id` concrets par produit cartésien des valeurs :

```
Template "pull"
  axe taille : {M, L}
  axe couleur : {rouge, noir}
→ 4 SKUs générés : PULL-M-ROUGE, PULL-M-NOIR,
                    PULL-L-ROUGE, PULL-L-NOIR
```

**Règles :**

- Un SKU peut être désactivé sans supprimer le template.
- Convention de nommage `article_id` paramétrable (template
  `{TEMPLATE}-{AXE1}-{AXE2}`).
- Idempotence : re-génération n'écrase pas les SKUs existants.

#### EF-44 — BOM et routing variantes

Chaque axe peut moduler la BOM et le routing du SKU :

- **BOM variant** : override d'un composant selon axe
  (ex : couleur rouge → dye_id = D-RED).
- **Routing variant** : override d'une opération selon axe
  (ex : taille L → temps machine +10%).
- **Coefficient variant** : override d'un `qty_per` ou
  `unit_time_min`.

**Modèle :**

- `bom_variant_override` : (template, axe, valeur, composant_id
  substitué, qty_per).
- `routing_variant_override` : (template, axe, valeur, sequence_idx,
  ws_id, unit_time_min_override).

La nomenclature aplatie (§2.3.0) et la gamme aplatie (§2.3.1) doivent
consolider automatiquement les overrides applicables à un SKU donné.

#### EF-45 — Réservation de capacité multi-variantes

Une SO peut mixer des variantes du même template (ex : 100 PULL-M-NOIR
+ 50 PULL-L-ROUGE). Le CRP consolide la charge sur la gamme aplatie de
chaque SKU en respectant leurs éventuels overrides.

---

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

### 3.6 Intégration et interopérabilité

#### 3.6.1 Intégrations métier

| Système | Sens | Contenu |
|---|:-:|---|
| ERP | ← | SOs, POs, stocks, articles, BOM |
| ERP | → | Livraisons, mouvements stock |
| GMAO | ← | Pannes machines, disponibilité, maintenance prév. |
| GMAO | → | Alertes anomalies, demandes intervention |
| Qualité (LIMS) | ← | Résultats contrôles, NC |
| MES terrain (tablettes) | ↔ | Saisie opérateurs, écrans postes |
| PDM/PLM | ← | Templates articles, axes variantes, BOM techniques |

#### 3.6.2 Normes et protocoles

Le système doit se conformer aux standards industriels de référence
pour crédibilité et interopérabilité :

- **ISA-95** : hiérarchie fonctionnelle. Le module APS positionné
  Level 4 (planification), le MES Level 3 (exécution). Les échanges
  entre niveaux respectent le modèle B2MML (Business To Manufacturing
  Markup Language) pour les objets `Production Schedule`,
  `Production Performance`, `Material Consumed/Produced`.
- **OPC-UA** : intégration temps réel avec SCADA / automates /
  machines connectées. Le MES doit publier un serveur OPC-UA exposant
  les états de workstation et souscrire aux événements physiques
  (start op, finish op, alarme).
- **ISO 9001** : traçabilité audit et gouvernance.
- **ISO 27001** : sécurité de l'information (cf. §3.4).

### 3.7 Résilience et mode dégradé

Le système doit garantir la continuité d'exploitation en cas
d'indisponibilité partielle.

#### 3.7.1 Mode dégradé MES sans APS

Si l'APS est down :

- Le MES continue sur l'agenda goulot déjà publié pendant N heures
  (`degraded_mode_max_hours`, défaut 24h).
- Les événements réels continuent d'être capturés dans le journal
  local.
- Les décisions locales (`action_level ∈ {inform, watch,
  correct_local}`) restent opérables.
- Les décisions `replan_local` et supérieures sont mises en file
  d'attente.

#### 3.7.2 Réconciliation au retour APS

- À la reprise de l'APS, les événements accumulés sont rejoués via le
  moteur event sourcing.
- Les file d'attente `replan_*` sont traitées en priorité.
- Un rapport de sortie de mode dégradé est produit (durée,
  événements traités, décisions retardées).

#### 3.7.3 Sauvegarde et RPO/RTO

| Métrique | Cible |
|---|:-:|
| RPO (Recovery Point Objective) | ≤ 24h |
| RTO (Recovery Time Objective) | ≤ 4h |
| Fréquence backups | Nightly + WAL streaming continu |
| Test restauration | Trimestriel |

#### 3.7.4 Alertes santé système

- Dashboard santé publie latences, files d'attente, erreurs.
- Seuils d'alerte : latence traitement déviation > 2×cible,
  file d'attente approbations > 50, erreurs > 5%/h.
- Notification administrateur + escalade DSI si dégradation
  prolongée.

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

## 6bis. Roadmap incrémentale de déploiement (Étapes 0 à 5)

L'implantation de la couche de gestion événementielle est **découpée
en 6 étapes** (0 à 5) qui livrent chacune une valeur mesurable et
constituent des points de sortie possibles selon le ROI observé.

Cette progression est validée par l'étude expérimentale de
composition additive : chaque étape ajoute une brique et le passage
Étape 0 → Étape 1 capture à lui seul ~90 % du gain nervosité mesuré.

**Note terminologique** : ces étapes de déploiement (0-5) ne doivent
pas être confondues avec les gates doctrinales P1-P4 (promotion,
lissage, freeze, clôture) qui constituent le cycle interne d'une
demande.

### Étape 0 — Baseline OF pur

- **Livrable** : mesures QCDS de référence sur ligne existante.
- **Valeur** : identifier écart avec objectifs cibles.
- **Coût** : nul (état actuel).
- **Durée** : 2-4 semaines de mesure.

### Étape 1 — Event capture + boucle physique corrective

- **Livrable** : capture continue expected/actual + boucle
  `_apply_corrective_actions` (L5.2 + L8.1).
- **Valeur** : ~ **90 % du gain final** en nervosité (−80 %) ;
  base pour toutes les étapes suivantes.
- **Coût** : enrichissement MES (start/finish OP, saisie NC),
  formation opérateurs.
- **Durée** : 8-12 semaines.

### Étape 2 — Filtre dual tolérances (data-driven)

- **Livrable** : filtre dual `evaluate_dual_tolerance` avec seuils
  calibrés en table `parameters` ; actions correct_local /
  replan_local wireées.
- **Valeur** : **Δ€/u −4 %**, ΔOTIF +0.4 pp (mesuré ablation × 4
  niveaux stress).
- **Coût** : calibration seuils avec équipe planification ; UX
  planificateur pour surcharger action.
- **Durée** : 4-6 semaines.

### Étape 3 — Matrice causes racines + apprentissage boucle longue

- **Livrable** : base R-RC-XX (règles), UI consultation matrice,
  `update_parameter_from_learning` gouverné.
- **Valeur** : capitalisation RETEX ; suggestions d'ajustement
  paramètres data-driven.
- **Coût** : constitution base de règles ; gouvernance MDM sur les
  paramètres actifs.
- **Durée** : 6-8 semaines.

### Étape 4 — Filtre dual mémoire + V13.C skip-latency

- **Livrable** : capture P4 systématique, `try_memory_shortcut`
  actif sous flag.
- **Valeur** : préparation maturité data-driven long terme
  (bénéfice marginal en volume court, mesurable après 6-12 mois
  d'exploitation).
- **Coût** : volumétrie historique ; gouvernance des recettes
  retenues.
- **Durée** : 4 semaines + montée en charge.

### Étape 5 — Delta engine + matrice sélecteur d'algo

- **Livrable** : `delta_engine` de V12.3 (deltas + queue
  d'approbation) + matrice sélecteur V12.5 (fast / balanced /
  quality).
- **Valeur** : automatisation des approbations pour actions
  répétitives ; adaptation profil algo au contexte.
- **Coût** : workflow humain / audit log ; formation superviseurs.
- **Durée** : 6-8 semaines.

### Tableau récapitulatif ROI par étape

| Étape | Valeur cumulée | Coût | Durée | Point d'arrêt possible |
|:-:|---|:-:|:-:|:-:|
| 0 | Mesure baseline | Nul | 2-4 sem | — |
| **1** | **−80 % nervosité** | Fort | 8-12 sem | Non (fondation) |
| 2 | −4 % €/u | Moyen | 4-6 sem | Oui si ROI atteint |
| 3 | RETEX + amélioration continue | Moyen | 6-8 sem | Oui |
| 4 | Maturité data-driven | Faible | 4 sem+ | Oui |
| 5 | Automatisation avancée | Moyen | 6-8 sem | Oui |

Étape 1 est **impérative** ; les étapes 2 à 5 sont **capitalisables
indépendamment** et peuvent être livrées selon la maturité de
l'organisation.

## 7. Hors périmètre et roadmap V2

### 7.1 Hors périmètre définitif (choix doctrinal)

Sont exclus **définitivement** de la cible OF+EVENT en raison des
résultats expérimentaux :

- Contractualisation flux hebdomadaire (V13.I).
- Jumeau numérique 5 flux persisté (V13.J).
- Zone négociable enrichie flux (V13.K).
- Boucle cybernétique étendue (BCE).

### 7.2 Hors périmètre V1 mais roadmap V2 (à réévaluer)

Ces fonctionnalités sont hors périmètre du présent CdC (V1) mais
identifiées comme cibles pour une V2 selon retour d'exploitation :

#### 7.2.1 Multi-site avec transferts inter-sites

- Modèle : plusieurs sites (`site_id`) chacun avec ses workstations,
  stocks, calendriers.
- Transferts inter-sites : composants ou semi-finis migrent d'un site
  à l'autre avec délai et coût.
- MRP et CRP consolidés multi-site (pegging traverse les sites).
- Optimisation allocation SO → site (charge, coût, proximité client).

**Impact V2** : refonte modèle de données (ajout `site_id` partout),
extension MRP et CRP, nouveaux moteurs d'allocation.

### 7.3 Hors périmètre système (non-cible)

- Fonctions de mise en marché (CRM, e-commerce, configurateur client).
- Gestion financière (comptabilité, facturation, paie).
- Gestion RH complète (formation, entretiens, paie).
- Traçabilité fine par numéro de série (à évaluer selon secteur).

## 8. Références

- `cadrage_of_event.md` — décision doctrinale et périmètre.
- `user_stories_of_event.md` — stories par rôle.
- `of_flux_event_bce_report.md` — étude stress fort.
- `of_flux_event_bce_extreme_report.md` — étude stress extrême.
- `cadrage_v4.md` — cadrage doctrinal complet (référence académique).
- `paper_hal_v1.md` — article de recherche associé.
