# pilotage-flux

Test de faisabilite V0 d'une solution APS + MES en pilotage par flux lean,
sur donnees reelles et avec event sourcing.

Voir `cadrage_cdc_solution_aps_mes_pilotage_flux_v2.docx` pour la doctrine et
le periemetre detaille du V0 (§21 bis).

## Etat

**V0 (Lots L0.1 à L0.6) complet.** Le golden path bout-en-bout fonctionne :
demande -> CBN -> contrats OF (P1) -> execution MES -> cloture P4 ->
reconstruction event-sourcee.

33 tests pytest, dont un test d'acceptation end-to-end couvrant les 3 criteres
de succes V0 (bout-en-bout, data-driven, tracabilite reconstructible).

## Setup (Windows, PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
```

## Golden path complet

```powershell
# 1. Initialiser une base et importer les referentiels
python -m pilotage_flux init-db --run demo --drop
python -m pilotage_flux import-refs --run demo

# 2. APS : CBN + charge/capacite + creation contrats OF (porte P1)
python -m pilotage_flux plan --run demo

# 3. MES : execution complete d'un OF (lancement -> ops -> cloture P4)
python -m pilotage_flux simulate-execution --run demo --of OF-0001

# 4. Visualisation
python -m pilotage_flux flow --run demo
python -m pilotage_flux of-detail --run demo --of OF-0001
python -m pilotage_flux events --run demo

# 5. Preuve event sourcing : reconstruction de l'OF depuis les seuls evenements
python -m pilotage_flux replay --run demo --of OF-0001
```

## Architecture

```
src/pilotage_flux/
  db/           # Schema SQLite (12 tables) + connexion
  events/       # Event store immuable + reconstruction
  importers/    # Import CSV referentiels
  parameters.py # Accesseurs data-driven (capacite, rendement, seuils)
  aps/          # CBN, charge/capacite, planner (creation contrat OF)
  mes/          # Lancement, declarations terrain, cloture
  gates/        # Portes P1 (creation OF) et P4 (cloture)
  visualization/# Vues flux physique par poste et par OF
  cli/          # CLI typer (init-db, plan, simulate-execution, ...)
data/
  fixtures/     # CSV golden path (3 articles, 3 postes, 2 commandes)
  runs/         # Bases SQLite generees (gitignored)
tests/          # 33 tests pytest dont test_acceptance_golden_path
```

## Periemetre V0 (rappel)

**Inclus** : implantation lineaire 3 postes, BOM mono-niveau, commandes
fermes uniquement, CBN basique, charge/capacite par poste avec parametres
SQLite versionnes, contrat OF avec operations planifiees, porte P1, MES
(lancement / declarations / clotures), porte P4, 5 types d'evenements
(OF_CREATED, OF_LAUNCHED, OP_STARTED, OP_FINISHED, OF_CLOSED), visualisation
flux physique, reconstruction event-sourcee.

**Reporte** : contrats de flux, zones et portes P2/P3, P3 inverse, fragments,
risk debt, filtre dual, evenements attendus vs reels, causes racines
ponderees, BOM multi-niveau, pegging, CPM, implantations parallele et
hybride, qualite, logistique, UI riche, multi-utilisateurs.

## Criteres de succes V0 (§21 bis.5)

Le test d'acceptation `tests/test_acceptance_golden_path.py` valide :

1. **Bout-en-bout** : une commande devient un OF, est executee et cloturee.
2. **Data-driven prouve** : modifier `capacity_factor` en base change le
   verdict de surcharge sans toucher au code.
3. **Tracabilite reconstructible** : `reconstruct_of` reconstruit l'etat
   complet depuis les 9 evenements du store, sans lire les tables metier.
4. **Reproductibilite** : les fixtures CSV sont deterministes.
5. **Goulot dynamique** : WS-2 (`capacity_factor=0.80`) est correctement
   identifie comme surcharge (450 min charge vs 384 min capacite).

## Suite (hors V0)

L0.6 marque la fin du V0. Suite recommandee selon le document de cadrage v2 :

- **V1** : portes P2 et P3, zones libre/negociable/gelee, contrats de flux,
  risk debt, P3 inverse, BOM multi-niveau, pegging.
- **V2** : MES complet (qualite, logistique, consommations), implantations
  parallele et hybride, fragments.
- **V3** : evenements attendus vs reels, filtre dual de tolerances et de
  memoire, causes racines bayesiennes, apprentissage.
