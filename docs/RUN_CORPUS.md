# Re-run du corpus complet — guide Windows

Orchestrateur unique : `docs/run_full_corpus.py`. Ré-exécute **tout le
corpus historique** (13 études + 6 figures + 9 diagnostics + 2 DOCX)
en un seul bloc.

## Lancer (PowerShell ou CMD, depuis la racine du dépôt)

```powershell
# 1. Activer l'environnement virtuel (adapter le chemin)
.\.venv\Scripts\activate

# 2. (si nécessaire) installer les dépendances
pip install -e .

# 3. Lancer tout le corpus
python docs\run_full_corpus.py
```

## Options utiles

```powershell
# Voir le plan sans rien lancer
python docs\run_full_corpus.py --list

# Version courte : sans les diagnostics
python docs\run_full_corpus.py --skip-diagnostics

# N'exécuter qu'un sous-ensemble (motif sur le nom de fichier)
python docs\run_full_corpus.py --only qcds
python docs\run_full_corpus.py --only v12_

# Sauter un script précis
python docs\run_full_corpus.py --skip validity

# S'arrêter à la première erreur (défaut : continuer)
python docs\run_full_corpus.py --stop-on-error
```

## Ce qui est régénéré

| Phase | Sorties |
|---|---|
| **etudes** | `docs/cadrage_v4_*_data.md`, tables QCDS, comparatives V12.x/V13.x |
| **figures** | `docs/charts/*.png`, `docs/*.xlsx` |
| **diagnostics** | traces console (pas de fichier) |
| **documents** | `docs/cadrage_v4.docx`, `docs/paper_hal_v1.docx` |

## Notes

- **Durée** : ~30-60 min pour le bloc complet selon la machine (chaque
  étude comparative = 100-480 runs de simulation). `--skip-diagnostics`
  fait gagner du temps.
- **Robustesse** : chaque script tourne dans un sous-processus isolé ;
  un échec n'interrompt pas le bloc (sauf `--stop-on-error`). Le
  récapitulatif final liste les scripts en échec avec la dernière ligne
  d'erreur.
- **Windows / accents** : l'orchestrateur force `PYTHONUTF8=1` et
  `MPLBACKEND=Agg` (pas de fenêtre matplotlib requise). Aucune config
  manuelle nécessaire.
- **OR-Tools** : les études faisant intervenir la doctrine OF_MILP
  (`build_validity_studies.py`) nécessitent `ortools`. Si absent, ce
  script apparaîtra en ÉCHEC dans le récap sans bloquer le reste —
  `pip install ortools` pour le réparer.
- **Code de sortie** : 0 si tout passe, 1 si au moins un script a
  échoué (utile pour CI / scripts).

## Métrique de coût corrigée (§28.19)

Depuis l'audit forensique, le KPI `cost_per_unit_delivered`
(= coût total / quantité livrée) est la métrique de coût **correcte**
pour comparer des doctrines à volumes différents. Le coût total brut
est trompeur (sous-produire le diminue artificiellement). Les études
régénérées exposent ce champ dans le `KpiSet`.
