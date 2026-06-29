# pilotage-flux

Solution APS + MES en pilotage par flux lean, sur données réelles SQLite avec
event sourcing. Voir `cadrage_cdc_solution_aps_mes_pilotage_flux_v2.docx` pour
la doctrine (§7 bis glossaire formel, §21 bis MVP V0).

## État

**V0** (L0.1 → L0.6), **V1** (L1.1 → L1.7) et **V2** (L2.1 → L2.7) complets.

- 201 tests pytest verts, dont trois tests d'acceptation end-to-end :
  - `test_acceptance_golden_path` V0 mono-niveau (data-driven + event-sourcing)
  - `test_acceptance_v1` multi-niveau (contrats de flux + freeze + P3 inverse)
  - `test_acceptance_v2` MES enrichi (stocks/PO + consommations + qualité + logistique + alternatives)

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

## Reporté à V3

- Événements attendus vs réels (couche événementielle V3 complète).
- Filtre dual de tolérances et de mémoire (cf. §7 bis.4 et §7 bis.5 du cadrage).
- Causes racines bayésiennes pondérées.
- Apprentissage automatique des seuils.

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
