# pilotage-flux

Solution APS + MES en pilotage par flux lean, sur données réelles SQLite avec
event sourcing. Voir `cadrage_cdc_solution_aps_mes_pilotage_flux_v2.docx` pour
la doctrine (§7 bis glossaire formel, §21 bis MVP V0).

## État

**V0** (L0.1 → L0.6), **V1** (L1.1 → L1.7), **V2** (L2.1 → L2.7), **V3** (L3.1 → L3.7), **V4** (L4.1 → L4.3), **V5** (L5.1 → L5.2), **V6** (L6.1 → L6.2) et **V7** (L7.1) complets.

- 270 tests pytest verts, dont six tests d'acceptation end-to-end :
  - `test_acceptance_golden_path` V0 mono-niveau (data-driven + event-sourcing)
  - `test_acceptance_v1` multi-niveau (contrats de flux + freeze + P3 inverse)
  - `test_acceptance_v2` MES enrichi (stocks/PO + consommations + qualité + logistique + alternatives)
  - `test_acceptance_v3` couche événementielle (attendus / matching / CPM / causes / dual tolérance / mémoire)
  - `test_acceptance_v4` étude comparative (OF / FLUX / EVENT sur même scénario, KPIs §19)
  - `test_acceptance_v5` variance multi-seeds + scénarios stress + V3 actionnel

## Setup (Windows, PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
```

## Golden path V0 (mono-niveau, OF-driven)

```powershell
python -m pilotage_flux init-db --run demo --drop
python -m pilotage_flux import-refs --run demo
python -m pilotage_flux plan --run demo                       # APS + P1
python -m pilotage_flux simulate-execution --run demo --of OF-0001
python -m pilotage_flux flow --run demo
python -m pilotage_flux replay --run demo --of OF-0001        # Event sourcing
```

## Golden path V1 (multi-niveau, doctrine flux complète)

```powershell
python -m pilotage_flux init-db --run v1 --drop
python -m pilotage_flux import-refs --run v1 --fixtures data/fixtures_v1
python -m pilotage_flux flatten-bom --run v1                  # L1.1
python -m pilotage_flux plan --run v1                         # CBN multi-niveau + P1
python -m pilotage_flux pegging --run v1 --so SO-001          # Chaîne demande -> composants
python -m pilotage_flux p2 --run v1                           # L1.3 - PASS_WITH_RISK + risk_debt
python -m pilotage_flux risk-debt --run v1                    # Liste risk_debts
python -m pilotage_flux flux-create --run v1 --horizon W27 --start 2026-07-06 --end 2026-07-12 --candidates "CND-0001,CND-0002,CND-0003,CND-0004"
python -m pilotage_flux flux-detail --run v1 --id FX-0001
python -m pilotage_flux flux-check --run v1 --id FX-0001      # Cohérence charge + takt
python -m pilotage_flux flux-smooth --run v1 --id FX-0001     # Distribution lissée
python -m pilotage_flux extinguish-debt --run v1 --id 1 --reason "test"
python -m pilotage_flux extinguish-debt --run v1 --id 2 --reason "test"
python -m pilotage_flux extinguish-debt --run v1 --id 3 --reason "test"
python -m pilotage_flux extinguish-debt --run v1 --id 4 --reason "test"
python -m pilotage_flux p3 --run v1 --id FX-0001              # FREEZE -> tranche gelée
python -m pilotage_flux freeze-list --run v1
python -m pilotage_flux zones --run v1                        # Tous en zone gelée

# P3 inverse Forme A : renégociation d'un OF non lancé
python -m pilotage_flux p3-return --run v1 --candidate CND-0001 --reason "renégo client"

# P3 inverse Forme B : fragmentation d'un OF lancé
python -m pilotage_flux mes-launch --run v1 --of OF-0002
python -m pilotage_flux p3-fragment --run v1 --of OF-0002 --qty 30 --reason "urgence"
python -m pilotage_flux lineage --run v1 --of OF-0002
```

## Architecture

```
src/pilotage_flux/
  db/               Schema SQLite + connexion
  events/           Event store + reconstruction event-sourcée
  parameters.py     Accesseurs data-driven (capacité, rendement, seuils)
  importers/        Import CSV référentiels
  aps/              CBN multi-niveau, charge/capacité, BOM aplatissement,
                    pegging multi-niveau, planner (création OF)
  mes/              Lancement, déclarations début/fin, clôture
  zones/            3 zones (libre/négociable/gelée) + cycles territoriaux
  rules/            Moteur de règles minimal SQLite-based (engine + evaluators)
  risk_debt.py      Registre risk_debts avec deadline + extinction
  flux/             Contrats de flux versionnés (contracts/coherence/smoothing/freeze)
  gates/            Portes P1, P2, P3, P3 inverse (forme A + B)
  visualization/    Vues flux physique par poste et par OF
  cli/              CLI typer (28 commandes)
data/
  fixtures/         Golden path V0 mono-niveau
  fixtures_v1/      Golden path V1 multi-niveau (ART-A→SEMI-1→COMP-X/Y)
  runs/             Bases SQLite générées (gitignored)
tests/              146 tests : unitaires + intégration + acceptation
```

## Concepts doctrinaux implémentés (cadrage §6, §7 bis)

| Concept | Module | État |
|---|---|---|
| Zones libre/négociable/gelée | `zones/transitions.py` | V1 ✓ |
| Cycles territoriaux P2/P3 | `zones/cycles.py` | V1 ✓ |
| Moteur de règles data-driven | `rules/` | V1 ✓ (5 règles P2, 4 règles P3) |
| Risk debt + extinction | `risk_debt.py` | V1 ✓ |
| Contrat de flux versionné | `flux/contracts.py` | V1 ✓ |
| Cohérence (charge + takt vs goulot) | `flux/coherence.py` | V1 ✓ |
| Lissage hebdomadaire | `flux/smoothing.py` | V1 ✓ |
| Tranche gelée immuable | `flux/freeze.py` | V1 ✓ |
| Porte P1 (création OF) | `gates/p1.py` | V0 ✓ |
| Porte P2 (qualification) | `gates/p2.py` | V1 ✓ |
| Porte P3 (freeze) | `gates/p3.py` | V1 ✓ |
| P3 inverse forme A (retour négo) | `gates/p3_inverse.py` | V1 ✓ |
| P3 inverse forme B (fragment) | `gates/p3_inverse.py` | V1 ✓ |
| Porte P4 (clôture) | `mes/closure.py` | V0 ✓ |
| Event sourcing + reconstruction | `events/` | V0 ✓ |
| Pegging multi-niveau | `aps/pegging.py` | V1 ✓ |
| Événements attendus | `events_v3/expected.py` | V3 ✓ |
| Matching attendu/réel + score | `events_v3/matching.py` | V3 ✓ |
| Absorption CPM niveau 0 | `events_v3/cpm.py` | V3 ✓ |
| Causes racines bayésiennes | `events_v3/root_causes.py` | V3 ✓ |
| Filtre dual de tolérances | `events_v3/dual_tolerance.py` | V3 ✓ |
| Filtre dual de mémoire (apprentissage) | `events_v3/dual_memory.py` | V3 ✓ |
| Étude comparative doctrinale (OF/FLUX/EVENT) | `comparative/` | V4 ✓ |
| V3 actionnel (close-loop physique) | `comparative/runner.py:_apply_corrective_actions` | V5 ✓ |
| Étude étendue variance multi-seeds | `comparative/variance.py` | V5 ✓ |
| Cohérence collective P3 multi-contrats | `gates/p3_collective.py` | V6 ✓ |
| 5 familles de flux (matière, qualité, décision, événement) | `visualization/` | V6 ✓ |
| Modèle de coûts data-driven (matière + MOD + MOI) | `costing/` | V7 ✓ |

## V2 — extensions livrées

- **Stocks + achats ouverts** (`stocks_purchasing/`) : table `stocks`,
  `purchase_orders`, helpers `project_available`, `reserve`, `receive_purchase`.
  Évaluateur R-P2-05 enrichi : PASS si projection (stock libre + PO ouverts)
  ≥ besoin pegging, sinon RISK ou BLOCK selon couverture.
- **Consommations matière** (`mes/consumptions.py`) : `declare_consumption`
  décrémente le stock + calcul d'écart via `compute_consumption_gaps` (réel
  vs théorique BOM × OF.quantity).
- **Qualité** (`quality/`) : plans de contrôle versionnés (`quality_controls`),
  événements qualité (`quality_events`) avec workflow complet open NC →
  rework → scrap → release, blocage critique.
- **Logistique** (`logistics/`) : emplacements typés (stock, ws_in, ws_out,
  shipping), événements `transfer`/`feed`/`evacuate`/`ship`/`receive`,
  calcul de file `queue_at(location_id)`.
- **Implantations parallèles/hybrides** (`aps/routing_alternatives.py`) :
  table `routing_alternatives` permet de déclarer plusieurs postes pour
  une même séquence d'opération, sélection par `pick_workstation` avec
  stratégie 'preferred' ou 'fastest'.

## V3 — extensions livrées (couche événementielle lean)

- **Événements attendus** (`events_v3/expected.py`) : `generate_expected_from_batch`
  produit, à partir d'une tranche gelée + lissage, les événements attendus
  (`op_start`, `op_finish`, `of_close`) avec horodatage prévisionnel.
- **Matching attendu/réel** (`events_v3/matching.py`) : apparie les MES events
  réels (OP_STARTED, OP_FINISHED, OF_CLOSED) à leurs attendus, calcule
  `delta_time` et `score` normalisés. Le seuil de tolérance temporelle est
  désormais **data-driven** via `parameters.matching_time_tolerance_minutes`.
- **Absorption CPM** (`events_v3/cpm.py`) : marge `cpm_margin_minutes`
  paramétrable ; les écarts en-dessous de la marge ne déclenchent pas d'action.
- **Causes racines bayésiennes** (`events_v3/root_causes.py`) : 6 règles
  seedées (panne, défaut qualité, rupture composant, retard logistique,
  modification demande, surcharge goulot) ; `attach_causes_to_deviation`
  applique un score `weight × confidence` à chaque déviation non absorbée.
- **Filtre dual de tolérances** (`events_v3/dual_tolerance.py` — §7 bis.4) :
  combine magnitude × récurrence sur fenêtre, mappe sur 6 niveaux d'action
  (inform → replan_global) avec latence configurable.
- **Filtre dual de mémoire** (`events_v3/dual_memory.py` — §7 bis.5) :
  capture la recette (écart + cause + décision + résultat) à la clôture P4,
  score significativité × récurrence, décide retenir/journaliser, et
  permet la mise à jour des paramètres (apprentissage).

## V4 — étude comparative doctrinale

Trois doctrines exécutent le **même scénario** (commandes + aléas datés +
seed déterministe) sur des bases SQLite séparées (cf. `comparative/`) :

| Doctrine | Périmètre |
|---|---|
| `of` | APS+MES OF-driven (V0) — pas de contrat de flux, pas de freeze, replan global sur chaque aléa |
| `flux` | APS+MES flux (V1+V2) — contrats, portes P2/P3, freeze, stocks/qualité/logistique mais pas d'event sourcing |
| `event` | APS+MES event sourcing (V3) — V1+V2 + expected/match/CPM/causes/dual tolerance |

KPIs mesurés (§19 cadrage) : lead time, WIP, recalculs APS, nervosité, écarts
détectés, actions filtre dual, replans locaux/globaux, causes attachées.

Exécution :

```powershell
python -m pilotage_flux compare-doctrines --report-path data/runs/comparative_report.md
```

Résultats sur le scénario `baseline` (15 jours, 3 SO ART-A, 4 aléas) :

| KPI | OF | FLUX | EVENT |
|---|---|---|---|
| Recalculs APS | 5 | 5 | **2** |
| Nervosité (replan/jour) | 0.333 | 0.333 | **0.133** |
| Lead time moyen (j) | 3.00 | 3.00 | **2.88** |
| Écarts détectés | 0 | 0 | **24** |
| Causes attachées | 0 | 0 | **72** |

## V5 — boucle physique + étude étendue

**L5.2 — V3 actionnel** : la doctrine V3 ferme la boucle planifier → exécuter
→ mesurer → **réguler** → apprendre. Quand le filtre dual de tolérances
déclenche une action de niveau `correct_local` ou supérieur ET qu'un poste
est en panne, V3 ordonne immédiatement la maintenance (clear breakdown).
Cela rend lead_time et WIP discriminants entre doctrines (cf. KPIs ci-dessous).

**L5.1 — Étude comparative étendue** : `comparative/variance.py` joue chaque
scénario sur N seeds avec jitter déterministe (timing ±1 jour, magnitudes ±20%)
pour mesurer la stabilité doctrinale. Quatre scénarios canoniques :

| Scénario | Description |
|---|---|
| `baseline` | 4 aléas mixtes (panne + NC + retard PO + urgence) |
| `stress_double_breakdown` | 2 pannes simultanées WS-1 et WS-3 (le goulot) |
| `stress_cascade_nc` | 3 NC qualité en cascade sur 3 jours |
| `stress_demand_spike` | 3 urgences clients en pic |

Exécution :

```powershell
python -m pilotage_flux compare-doctrines-extended --seeds "42,100,200,300,400"
```

Synthèse Δ V3 vs FLUX (5 seeds × 4 scénarios = 60 runs) :

| Scénario | Δ nervosité | Δ lead time (j) | Δ WIP | Écarts V3 |
|---|---|---|---|---|
| baseline | -0.200 | -0.200 | -0.108 | 24.0 |
| stress_double_breakdown | -0.111 | **-1.336** | 0 | 24.0 |
| stress_cascade_nc | -0.200 | 0 | 0 | 24.0 |
| stress_demand_spike | 0 | 0 | 0 | 24.0 |

→ **V3 sauve 1.3 jours de lead time** sur la double panne (le scénario où
le poste goulot WS-3 tombe en panne). V3 ne dégrade aucun scénario.
V3 ne change rien sur `stress_demand_spike` — résultat honnête : la doctrine
événementielle n'aide pas quand l'aléa est de la **demande nouvelle**, qui
exige un APS replan quoi qu'il en soit.

Rapports complets : `data/comparative_baseline_report.md` et
`data/comparative_extended_report.md`.

## V6 — cohérence collective P3 + 5 familles de flux

**L6.1 — P3 collective multi-contrats** (`gates/p3_collective.py`, §180.g) :
`run_p3_collective_freeze(conn, [c1, c2, ...])` évalue plusieurs contrats
sur le même horizon, calcule le **vrai goulot collectif** (= poste avec
plus haut ratio charge/capacité cumulée), puis décide :

| Décision | Condition |
|---|---|
| `FREEZE_ALL` | charge cumulée sur goulot ≤ capacité horizon |
| `PARTIAL_FREEZE` | surcharge → freeze par priorité (FIFO entrée négociable) jusqu'à saturation |
| `DEFER_ALL` | aucun contrat ne tient seul |

Une seule tranche gelée pour N contrats, traçabilité complète via
`gate_decisions_v1` (gate='P3_COLLECTIVE') et event_store.

```powershell
python -m pilotage_flux p3-collective --run v1 --contracts FX-0001,FX-0002
```

**L6.2 — 5 familles de flux** (`visualization/`, §12 cadrage) :

| # | Famille | Module | KPIs principaux |
|---|---|---|---|
| 1 | physique | `flow.py` (existant) | WIP, pending/running/done par poste |
| 2 | matière | `material.py` | stock + PO ouv. + conso vs théo BOM + écart |
| 3 | qualité | `quality.py` | yield rate, NCs, blocages, libérations |
| 4 | décisionnel | `decision.py` | décisions portes + zones + filtre dual |
| 5 | événementiel | `event.py` | attendus/réels matched, qualif, causes |

```powershell
python -m pilotage_flux flow-material  --run v1
python -m pilotage_flux flow-quality   --run v1
python -m pilotage_flux flow-decision  --run v1
python -m pilotage_flux flow-events    --run v1 --batch FZ-0001
```

## V7 — modèle de coûts (matière + MOD + MOI)

`costing/engine.py` chiffre chaque OF : matière (BOM × prix unitaire),
MOD (durée réelle d'op × taux horaire poste), MOI (overhead %), scrap
(qté × prix unitaire perdue). Tous les paramètres sont data-driven dans
la table `parameters` (`unit_cost`, `hourly_rate`, `moi_overhead_rate`,
`moi_fixed_per_of`). Un helper `seed_default_unit_costs(conn)` pose les
valeurs indicatives pour les fixtures V1 (idempotent).

```powershell
python -m pilotage_flux costs --run v1                  # breakdown par OF
python -m pilotage_flux costs --run v1 --of-id OF-0001  # détail d'un OF
```

L'étude comparative étendue inclut désormais le **Δ coût en €** :

| Scénario | Δ nervosité | Δ lead time (j) | Δ WIP | Δ coût (€) | Détections V3 |
|---|---|---|---|---|---|
| baseline | -0.200 | -0.200 | -0.108 | **-7862** | 24.0 |
| stress_double_breakdown | -0.111 | -1.336 | 0 | **-7925** | 24.0 |
| stress_cascade_nc | -0.200 | 0 | 0 | 0 | 24.0 |
| stress_demand_spike | 0 | 0 | 0 | 0 | 24.0 |

→ **V3 économise ~8000 €/run** sur les scénarios pannes (baseline et double
breakdown). Comme MOD = durée réelle × taux horaire, V3 capture en € l'effet
de sa boucle physique : moins de temps machine bloqué = moins de MOD facturée.
Sur cascade_nc et demand_spike, V3 ne touche pas au coût (cohérent : la
boucle physique L5.2 ne traite que les pannes, pas les NCs ni les nouvelles
demandes).

## Critères de succès validés par tests d'acceptation

`tests/test_acceptance_golden_path.py` (V0) :
1. Bout-en-bout demande → OF clôturé
2. Data-driven prouvé (`UPDATE parameters` change comportement)
3. Reconstruction event-sourcée

`tests/test_acceptance_v1.py` (V1) :
4. Pegging multi-niveau (100 ART-A → 200 COMP-X via 100 SEMI-1)
5. Moteur de règles P2 (5 critères data-driven + risk_debt)
6. Contrat de flux versionné + cohérence + lissage
7. P3 freeze (tranche gelée immuable, snapshot version)
8. P3 inverse forme A (retour négociable + OF annulé)
9. P3 inverse forme B (fragmentation, conservation quantité, filiation)
10. Traçabilité event_store (8 types d'événements) + gate_decisions_v1

`tests/test_acceptance_v2.py` (V2) :
11. Stocks initiaux + PO ouvert → P2 PASS quand projection couvre le besoin
12. Réception PO incrémente le stock
13. Consommations matière déclenchent décrément stock + écart matière nul
    quand conso = BOM × OF.quantity
14. Workflow qualité : contrôle PASS + libération tracés
15. Workflow logistique : feed + ship + queue calculée correctement
16. Routings alternatifs déclarables, pick_workstation déterministe

`tests/test_acceptance_v3.py` (V3) :
17. Génération expected_events depuis tranche gelée (16 events / 4 candidates)
18. Matching attendu/réel produit des deviations qualifiées (low/medium/high/critical)
19. Absorption CPM (marge data-driven via parameters.cpm_margin_minutes)
20. Causes racines attachées (6 règles seedées disponibles)
21. Filtre dual de tolérances : décision par déviation avec action_level valide
22. Filtre dual de mémoire : capture recette + score + décision retain/log_only
23. Apprentissage : mise à jour data-driven d'un paramètre depuis une recette retenue

`tests/test_acceptance_v4.py` (V4 — étude comparative) :
24. Les 3 doctrines (OF/FLUX/EVENT) terminent le même scénario sans erreur
25. Seul V3 détecte des écarts (deviations_detected > 0)
26. Seul V3 attache des causes (causes_attached > 0)
27. V3 produit des actions proportionnées (non-globales)
28. Nervosité V3 ≤ nervosité V1+V2 ≤ nervosité OF (par construction)
29. Même nombre d'OF clôturés entre doctrines
30. Reproductibilité : 2 runs successifs même doctrine → mêmes KPIs

`tests/test_acceptance_v5.py` (V5 — variance + boucle physique) :
31. 4 scénarios × 3 doctrines × 3 seeds = 36 runs sans erreur
32. V3 détecte des écarts et attache des causes sur les 4 scénarios
33. V3 sauve du lead time vs FLUX sur `stress_double_breakdown` (boucle physique L5.2)
34. V3 nervosité ≤ FLUX nervosité sur `baseline`
35. Rapport étendu Markdown construit pour les 4 scénarios
