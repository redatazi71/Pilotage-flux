# User Stories — Système APS+MES événementiel piloté par OF

**Version** : 1.0
**Date** : 2026-07
**Doctrine** : OF+EVENT
**Références** : `cadrage_of_event.md`, `cahier_des_charges_of_event.md`

---

## Découpage par zones décisionnelles

Les user stories sont ancrées sur les **trois zones** définies dans le
cadrage :

| Zone | Domaine | Epics associés |
|---|---|---|
| **Zone libre** | Prévision, ATP/CTP, réservation, confirmation | 1, 2 |
| **Zone négociable** | Planification, lissage, ordonnancement, signature contrat | 3 |
| **Zone gelée** | MES, event sourcing, filtres duals, clôture | 4, 5, 7 |
| Transverse | Apprentissage, supervision, non-fonctionnel | 6, 8, 9 |

Chaque story est marquée **[L]** (libre), **[N]** (négociable),
**[G]** (gelée) ou **[T]** (transverse) dans son titre.

---

## Personas

### P1 — Direction industrielle (Sponsor)

- **Priorités** : OTIF, marge, résilience.
- **Fréquence usage** : hebdomadaire (dashboards).
- **Compétence système** : faible.

### P2 — Planificateur APS

- **Priorités** : engagement délais, équilibrage charge, minimisation
  nombre d'OFs.
- **Fréquence usage** : quotidienne intensive.
- **Compétence système** : experte.

### P3 — Chef d'atelier / Superviseur

- **Priorités** : livraison quotidienne, arbitrage aléas, sécurité,
  équipes.
- **Fréquence usage** : continue (temps réel).
- **Compétence système** : bonne (dashboards + décisions).

### P4 — Opérateur atelier

- **Priorités** : exécution opération, qualité, temps standard.
- **Fréquence usage** : continue (saisie MES).
- **Compétence système** : basique (UI simple obligatoire).

### P5 — Contrôleur qualité

- **Priorités** : détection NC, arbitrage rework/scrap, conformité.
- **Fréquence usage** : ponctuelle sur événement.
- **Compétence système** : moyenne.

### P6 — Acheteur / Logisticien

- **Priorités** : PO en cours, délais fournisseurs, stock composants.
- **Fréquence usage** : quotidienne.
- **Compétence système** : moyenne.

### P7 — Administrateur système

- **Priorités** : disponibilité, intégrations, paramètres.
- **Fréquence usage** : hebdomadaire ou sur incident.
- **Compétence système** : experte.

---

## Epic 1 [L] — Prévision et engagement de délai (Zone libre)

### US-01 [L] (P2) — Estimer un délai ATP simple

**En tant que** planificateur,
**je veux** obtenir instantanément la date de disponibilité au plus
tôt pour une demande client,
**afin de** répondre au commercial sans attendre.

**Critères d'acceptation :**

- Saisie (article, quantité, date souhaitée) → réponse en < 500 ms.
- La réponse indique : `promised_date`, `available_qty`,
  `confidence_score`, sources (stock, OFs, POs).
- Si `available_qty < quantity`, le système bascule automatiquement
  sur CTP (US-02).

### US-02 (P2) — Estimer un délai CTP avec faisabilité industrielle

**En tant que** planificateur,
**je veux** simuler la faisabilité industrielle d'une demande
(décomposition BOM + ordonnancement),
**afin de** m'engager sur une date tenable.

**Critères d'acceptation :**

- La simulation identifie le goulot dynamique.
- Retour : `feasibility_date`, `takt_target_min`, `wip_predicted`,
  `rho_bottleneck`, `feasible: bool`.
- Temps de réponse ≤ 3 s pour une demande standard (≤ 5 niveaux BOM).
- Résultat persisté dans `flux_candidate_feasibility` (déjà existant).

### US-03 (P2) — Réserver de la capacité

**En tant que** planificateur,
**je veux** réserver des slots de capacité sur les workstations
concernées après validation CTP,
**afin de** bloquer la charge en attendant la confirmation client.

**Critères d'acceptation :**

- Réservation atomique : slots verrouillés vis-à-vis d'autres CTP.
- Réservation expirable (7j paramétrable) si non confirmée.
- Vue « réservations actives » consultable par planificateur.
- Log audit de chaque réservation / libération.

### US-04 (P3) — Voir les engagements pris

**En tant que** chef d'atelier,
**je veux** consulter la liste des délais engagés en attente de
confirmation,
**afin d'** anticiper la charge future.

**Critères d'acceptation :**

- Dashboard triable par date d'engagement, workstation, article.
- Filtre : réservations confirmées vs en attente.
- Affichage taux d'occupation goulot par jour sur l'horizon.

---

## Epic 2 [L→N] — Confirmation de commande et création OFs (bascule)

### US-05 (P2) — Confirmer une SO à partir d'une CTP

**En tant que** planificateur,
**je veux** convertir une CTP validée en SO ferme,
**afin de** lancer la production.

**Critères d'acceptation :**

- Conversion crée un `sales_order` + `demand_contract` lié.
- La réservation de capacité est convertie en OFs planifiés.
- L'agenda goulot est mis à jour (drum).
- Notification automatique achats si PO composants nécessaires.

### US-06 (P2) — Voir le contrat de demande d'une SO

**En tant que** planificateur,
**je veux** voir le contrat de demande d'une SO (cibles QCDS),
**afin de** suivre les engagements pris et détecter dérives.

**Critères d'acceptation :**

- Affichage : bottleneck_ws, takt_target_min, wip_target,
  rho_bottleneck, buffer_days, feasible, doc_status.
- Statut visible : `draft` / `signed` / `fulfilled`.
- Historique des révisions consultable.

### US-07 (P2) — Renégocier une SO

**En tant que** planificateur,
**je veux** modifier la quantité ou la date d'une SO non signée,
**afin de** répondre à une évolution du besoin client.

**Critères d'acceptation :**

- Modification autorisée seulement si `doc_status = draft`.
- Recalcul CTP automatique.
- Log audit avant/après.

---

## Epic 3 [N] — Planification APS (Zone négociable)

### US-08a [N] (P2) — Voir la nomenclature aplatie d'une SO

**En tant que** planificateur,
**je veux** consulter la nomenclature aplatie (flattened BOM) d'un
article fini demandé,
**afin de** comprendre l'ensemble des composants terminaux (semi-
finis et achetés) qui vont être générés.

**Critères d'acceptation :**

- Vue tableau : composant, coefficient consolidé, niveau BOM min,
  is_purchased.
- Filtrage / tri par coefficient, par niveau.
- Export CSV.

### US-08b [N] (P2) — Consulter les besoins nets (MRP)

**En tant que** planificateur,
**je veux** consulter les besoins nets calculés par composant et par
jour sur mon horizon,
**afin de** valider la charge composants et déclencher les PO / OFs
nécessaires.

**Critères d'acceptation :**

- Vue matricielle composant × jour.
- Détail ligne : besoin brut, stock disponible, PO ouverts, besoin
  net, pegging SO.
- Colonne « suggestion » : PO ou OF avec quantité + date.

### US-08c [N] (P2) — Voir le pegging d'un besoin net

**En tant que** planificateur,
**je veux** voir à quelles SOs un besoin net donné est rattaché
(pegging bottom-up),
**afin de** comprendre l'impact d'une renégociation ou d'un retard.

**Critères d'acceptation :**

- Détail ligne besoin net → liste `(sales_order_id, qty_attribuée,
  due_date)`.
- Total ventilé = besoin net (invariant).
- Lien clic vers chaque SO.

### US-08d [N] (P6) — Confirmer/refuser une suggestion PO issue du MRP

**En tant qu'** acheteur,
**je veux** voir en un endroit toutes les suggestions PO issues du
MRP et les valider ou refuser,
**afin de** garder le contrôle sur les commandes fournisseur.

**Critères d'acceptation :**

- File de suggestions triée par urgence (due − lead_time).
- Un clic « valider » → création PO.
- Un clic « refuser » avec motif → besoin net reste, alerte
  planificateur.

### US-08e [N] (P2) — Consulter la gamme aplatie d'un article fini

**En tant que** planificateur,
**je veux** consulter la gamme aplatie (flattened routing) d'un
article fini,
**afin de** comprendre les temps machine par workstation pour
produire une unité finie.

**Critères d'acceptation :**

- Vue : WS, temps total par unité finie (min), nb opérations
  agrégées, séquence amont.
- Comparaison graphique multi-articles.

### US-08f [N] (P2) — Voir la charge par workstation (CRP)

**En tant que** planificateur,
**je veux** consulter la charge par workstation et par jour sur
l'horizon,
**afin d'** identifier le goulot dynamique et anticiper les
surcharges.

**Critères d'acceptation :**

- Vue heatmap WS × jour, couleurs selon `taux_charge` (vert < 0.85,
  orange < 1.0, rouge ≥ 1.0).
- Détail cellule : charge_min, capacite_min, `is_bottleneck`.
- Bouton « voir OFs contributifs » (US-08g).

### US-08g [N] (P2) — Voir le pegging d'une charge WS

**En tant que** planificateur,
**je veux** voir quels OFs contribuent à la charge d'un WS un jour
donné, et via quels SOs,
**afin de** décider quel OF décaler en cas de surcharge.

**Critères d'acceptation :**

- Détail cellule CRP → liste OFs avec `sales_order_id`, temps
  contribué, priorité SO.
- Bouton « décaler cet OF » (déclenche re-planification locale).

### US-08h [N] (P2) — Alerte surcharge

**En tant que** planificateur,
**je veux** être alerté dès qu'une workstation dépasse 100% de
charge sur l'horizon signé,
**afin de** décider (décaler, sous-traiter, heures sup) avant que le
plan devienne infaisable.

**Critères d'acceptation :**

- Notification poussée (interface + email).
- Suggestion automatique : liste des OFs décalables (basse priorité)
  ou options capacité (heures sup, WS parallèle).

### US-08 [N] (P2) — Voir le plan actuel

**En tant que** planificateur,
**je veux** consulter le plan de production sur horizon glissant,
**afin de** vérifier l'ordonnancement et la charge par poste.

**Critères d'acceptation :**

- Vue Gantt filtrable par workstation, article, SO.
- Mise en évidence du goulot dynamique.
- Indicateurs : rho par poste, WIP prévisionnel, buffer.

### US-09 [N] (P2) — Re-planifier globalement

**En tant que** planificateur,
**je veux** déclencher un re-ordonnancement global en cas d'aléa
majeur ou d'ajout SO urgente,
**afin de** minimiser l'impact sur les engagements.

**Critères d'acceptation :**

- Bouton « replanifier » accessible depuis la vue plan.
- Simulation avant validation (dry-run).
- Diff clair : OFs déplacés, décalés, split, mergés.
- Temps de calcul ≤ 30 s pour 100 OFs.

### US-10 (P2) — Voir le nombre d'OFs par SO

**En tant que** planificateur,
**je veux** voir combien d'OFs ont été générés par SO,
**afin de** vérifier que le lot-sizing minimise ce nombre (objectif
QCDS).

**Critères d'acceptation :**

- Colonne « n_of » dans la vue SO.
- Alerte si n_of > seuil (paramétrable, défaut 5).

---

## Epic 4 [G] — Exécution atelier MES (Zone gelée)

### US-11 (P4) — Voir mon prochain travail

**En tant qu'** opérateur,
**je veux** voir sur ma tablette la prochaine opération à démarrer
sur mon poste,
**afin de** enchaîner sans temps mort.

**Critères d'acceptation :**

- Écran plein poste, affichage OF + opération + quantité + temps
  standard.
- Codes couleur : prêt, en attente composant, bloqué.
- Bouton « Démarrer » un clic.

### US-12 (P4) — Démarrer une opération

**En tant qu'** opérateur,
**je veux** démarrer une opération d'un clic,
**afin de** ne pas être ralenti par la saisie.

**Critères d'acceptation :**

- Un tap → `start_operation` appelé, horodatage enregistré.
- Vérification prérequis (composants OK) automatique.
- Si prérequis KO, message clair et notification chef d'atelier.

### US-13 (P4) — Terminer une opération avec qualité

**En tant qu'** opérateur,
**je veux** saisir la quantité bonne et rebut à la fin de l'opération,
**afin de** clôturer proprement.

**Critères d'acceptation :**

- Écran de fin : 2 champs (qty_good, qty_scrap), pré-remplis à
  quantité prévue / 0.
- Si `qty_scrap > seuil` (par article), demande motif NC.
- Un tap → `finish_operation` appelé, événement réel produit.

### US-14 (P3) — Voir les alertes atelier temps réel

**En tant que** chef d'atelier,
**je veux** être notifié en temps réel des alertes MES (breakdown,
NC critique, blocage composant),
**afin d'** arbitrer immédiatement.

**Critères d'acceptation :**

- Push notification (mobile + poste supervision).
- Historique consultable par workstation et par période.
- Filtrage par action_level (`escalate`, `replan_global` en tête).

### US-15 (P5) — Enregistrer un contrôle qualité NC

**En tant que** contrôleur qualité,
**je veux** créer un événement NC avec motif et sévérité,
**afin de** déclencher la boucle événementielle.

**Critères d'acceptation :**

- Saisie : article, quantité NC, sévérité (`normal|high|critical`),
  cause candidate.
- Enregistrement dans `actual_events` + `event_deviations`.
- Décision automatique via filtre dual tolérances.

---

## Epic 5 [G] — Boucle événementielle (Zone gelée)

### US-16 (P3) — Voir les déviations en cours

**En tant que** chef d'atelier,
**je veux** consulter la liste des déviations non résolues et leur
niveau d'action recommandé,
**afin de** hiérarchiser mes interventions.

**Critères d'acceptation :**

- Liste triable par action_level, workstation, ancienneté.
- Colonne « source décision » : `tolerance` vs `memory_shortcut`
  (V13.C).
- Filtre : déviations absorbées vs actives.

### US-17 (P3) — Comprendre pourquoi une action a été proposée

**En tant que** chef d'atelier,
**je veux** voir les causes racines candidates attachées à une
déviation,
**afin de** valider ou contester le diagnostic système.

**Critères d'acceptation :**

- Détail déviation → causes classées par score.
- Bouton « Confirmer cause X » disponible.
- Confirmation trace la validation dans l'audit log.

### US-18 (P3) — Comprendre un skip-latency mémoire

**En tant que** chef d'atelier,
**je veux** voir la recette apprise qui a court-circuité l'analyse
tolérance,
**afin de** m'assurer que l'application automatique reste pertinente.

**Critères d'acceptation :**

- Sur une décision `source='memory_shortcut'`, lien vers les
  recettes retenues source.
- Affichage : signature, nb d'occurrences retenues, outcomes.
- Bouton « désactiver skip-latency pour cette signature » si dérive
  détectée.

### US-19 (P3) — Appliquer une action corrective manuelle

**En tant que** chef d'atelier,
**je veux** pouvoir surcharger l'action recommandée par le système,
**afin de** garder la main sur les décisions critiques.

**Critères d'acceptation :**

- Sur une déviation en `escalate` : choix parmi les action_level.
- Justification obligatoire (texte libre + rule_id éventuel).
- Traçabilité complète (audit log).

### US-20 (P4) — Recevoir une consigne suite à une déviation

**En tant qu'** opérateur,
**je veux** recevoir sur ma tablette une consigne claire quand une
action corrective touche mon poste,
**afin d'** exécuter sans ambiguïté.

**Critères d'acceptation :**

- Push notification poste + affichage in-line dans le workflow.
- Consigne : action à réaliser, article/quantité, priorité.
- Acquittement obligatoire avant reprise.

---

## Epic 6 [T] — Apprentissage et gouvernance (Transverse)

### US-21 (P2) — Consulter les recettes apprises

**En tant que** planificateur,
**je veux** consulter le catalogue des recettes mémoire retenues,
**afin de** comprendre les patterns fréquents.

**Critères d'acceptation :**

- Liste des `memory_recipes` retenues, groupée par `deviation_kind`.
- Colonnes : signature, action_level majoritaire, nb occurrences,
  outcome distribution.
- Export CSV.

### US-22 (P2) — Valider une mise à jour de paramètre

**En tant que** planificateur,
**je veux** valider ou refuser une suggestion de mise à jour de
paramètre issue de l'apprentissage,
**afin de** garder le contrôle sur les seuils critiques.

**Critères d'acceptation :**

- Notification quand recette retenue suggère `update_rule`.
- Vue diff : old_value vs new_value + justification.
- Validation trace `memory_filter_decisions` avec `decision='update_rule'`.

### US-23 (P7) — Consulter les paramètres actifs

**En tant qu'** administrateur,
**je veux** voir tous les paramètres actifs du système et leur
historique,
**afin de** auditer la configuration.

**Critères d'acceptation :**

- Vue table `parameters` filtrable par scope et nom.
- Historique valid_from/valid_to.
- Export.

### US-24 (P7) — Ajuster manuellement un paramètre

**En tant qu'** administrateur,
**je veux** modifier manuellement un paramètre data-driven,
**afin de** répondre à un changement métier majeur.

**Critères d'acceptation :**

- Modification versionnée (nouveau enregistrement, ancien `valid_to`).
- Justification obligatoire.
- Audit log.

---

## Epic 7 [G] — Clôture demande (Zone gelée → sortie)

### US-25 (P4) — Clôturer un OF

**En tant qu'** opérateur (ou automatique),
**je veux** clôturer un OF quand toutes ses opérations sont terminées,
**afin de** libérer les ressources et alimenter la boucle mémoire.

**Critères d'acceptation :**

- Trigger automatique quand dernière opération `finished`.
- Capture recette mémoire (V13.C) systématique.
- Décrément / incrément stock automatique.
- Notification chef d'atelier.

### US-26 (P3) — Voir l'avancement d'une SO

**En tant que** chef d'atelier,
**je veux** voir en un coup d'œil l'état d'avancement d'une SO,
**afin d'** anticiper les livraisons.

**Critères d'acceptation :**

- Vue SO → progress bar par quantité livrée / quantité commandée.
- Statuts OFs liés (planifié, lancé, en cours, clôturé).
- Date de livraison prévue mise à jour dynamiquement.

### US-27 (P2) — Clôturer une SO

**En tant que** planificateur (ou automatique),
**je veux** clôturer une SO quand tous ses OFs sont clôturés et la
quantité livrée est atteinte,
**afin de** engager la facturation et le bilan.

**Critères d'acceptation :**

- Trigger automatique.
- Passage `demand_contract` en `fulfilled`.
- Génération du rapport de bilan (US-28).
- Notification client (optionnel, via ERP).

### US-28 (P1) — Consulter le bilan d'une demande clôturée

**En tant que** directeur industriel,
**je veux** consulter le bilan d'une SO clôturée (plan vs réel),
**afin de** mesurer la performance et alimenter le RETEX.

**Critères d'acceptation :**

- Rapport contient : OTIF réel, quantity_compliance, €/u réel vs
  prévu, nervosité, recovery si aléa, recettes capturées.
- Comparaison avec les cibles du contrat de demande.
- Export PDF.

---

## Epic 8 [T] — Pilotage et supervision (Transverse)

### US-29 (P1) — Dashboard OTIF hebdomadaire

**En tant que** directeur industriel,
**je veux** un dashboard OTIF actualisé toutes les semaines,
**afin de** suivre le service client.

**Critères d'acceptation :**

- Vue : OTIF global, tendance 12 sem, top 5 SOs en retard, taux
  rupture.
- Drill-down par famille produit / client.

### US-30 (P1) — Alerte rupture imminente

**En tant que** directeur industriel,
**je veux** être alerté quand une rupture livraison est probable,
**afin de** décider d'actions correctives ou de communication.

**Critères d'acceptation :**

- Détection basée sur `feasibility_date` glissant + aléas récents.
- Notification email + dashboard.
- Lien vers la SO concernée.

### US-31 (P6) — Suivre les PO composants

**En tant qu'** acheteur,
**je veux** voir l'impact d'un retard PO sur les OFs planifiés,
**afin de** relancer le fournisseur en priorité.

**Critères d'acceptation :**

- Vue PO → OFs bloqués (via composants).
- Alerte si `po_delay` détecté par event sourcing.
- Suggestion d'action corrective (re-planifier, sourcer alternatif).

---

## Epic 9 [T] — Non-fonctionnel (Transverse)

### US-32 (P7) — Reprise après incident

**En tant qu'** administrateur,
**je veux** pouvoir rejouer le journal d'événements depuis un point
donné,
**afin de** restaurer l'état du système après crash.

**Critères d'acceptation :**

- Event store immuable persisté.
- Fonction `replay(since_ts)` disponible.
- Cohérence garantie sur reprise.

### US-33 (P7) — Sauvegarder / restaurer

**En tant qu'** administrateur,
**je veux** sauvegarder l'état complet quotidiennement et pouvoir
restaurer,
**afin de** couvrir les scénarios de sinistre.

**Critères d'acceptation :**

- Backup automatique nightly.
- Restauration testée trimestriellement.
- RPO ≤ 24h, RTO ≤ 4h.

---

## Résumé — 41 stories couvrant 9 epics × 3 zones

| Epic | Zone | Nb US | Personas concernés |
|---|:-:|:-:|:-:|
| 1 — Prévision et engagement | L | 4 | P2, P3 |
| 2 — Confirmation SO | L→N | 3 | P2 |
| 3 — Planification APS (dont MRP+CRP+pegging) | N | 11 | P2, P6 |
| 4 — Exécution atelier | G | 5 | P3, P4, P5 |
| 5 — Boucle événementielle | G | 5 | P3, P4 |
| 6 — Apprentissage / gouvernance | T | 4 | P2, P7 |
| 7 — Clôture demande | G | 4 | P1, P2, P3, P4 |
| 8 — Pilotage / supervision | T | 3 | P1, P6 |
| 9 — Non-fonctionnel | T | 2 | P7 |
| **Total** | | **41** | |

## Répartition par zone

| Zone | Nb US | Contenu clé |
|---|:-:|---|
| **Libre (L)** | 7 | ATP/CTP, réservation, confirmation SO |
| **Négociable (N)** | 11 | Nomenclature aplatie, MRP, gamme aplatie, CRP, pegging, ordonnancement DBR |
| **Gelée (G)** | 14 | MES, event sourcing, filtres duals, clôture |
| **Transverse (T)** | 9 | Apprentissage, supervision, non-fonctionnel |

## Priorisation MVP

- **MVP0** (P0) : US-01, US-05, US-11, US-13, US-25, US-27.
- **MVP1** (P1) : US-02, US-03, US-06, US-08a-c (MRP + pegging),
  US-08, US-12, US-16, US-26.
- **MVP2** (P2) : US-04, US-07, US-08d-h (PO, CRP, pegging routing,
  alertes), US-09, US-14, US-15, US-17, US-28, US-29.
- **Enrichissement** (P3+) : reste des stories.
