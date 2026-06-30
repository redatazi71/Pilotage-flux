# Matrice QCDS 5 doctrines × 4 scénarios (Option 1)

Mesure des 4 objectifs QCDS sur la matrice doctrinale étendue.
5 seeds par cellule (3000-3004). Métriques moyennées.

## baseline_xl

| Doctrine | Q (quantity) | D (dispo SO) | OTIF | C (€) | Nervosité | WIP pic | WIP σ |
|---|---|---|---|---|---|---|---|
| OF | 0.950 | 1.000 | **0.950** | 41 139 € | 0.25 | 18.0 | 6.40 |
| OF+EVENT | 0.950 | 1.000 | **0.950** | 37 707 € | 0.10 | 18.0 | 6.40 |
| FLUX | 0.694 | 1.000 | **0.694** | 31 679 € | 0.25 | 9.8 | 2.04 |
| EVENT | 0.694 | 1.000 | **0.694** | 31 679 € | 0.10 | 9.8 | 2.04 |
| EVENT V13.0 | 0.924 | 1.000 | **0.924** | 36 807 € | 0.10 | 13.0 | 4.37 |

## stress_double_breakdown_xl

| Doctrine | Q (quantity) | D (dispo SO) | OTIF | C (€) | Nervosité | WIP pic | WIP σ |
|---|---|---|---|---|---|---|---|
| OF | 0.950 | 1.000 | **0.950** | 48 735 € | 0.14 | 18.0 | 6.23 |
| OF+EVENT | 0.950 | 1.000 | **0.950** | 35 544 € | 0.04 | 18.0 | 6.28 |
| FLUX | 0.675 | 1.000 | **0.675** | 29 941 € | 0.14 | 7.0 | 1.44 |
| EVENT | 0.675 | 1.000 | **0.675** | 27 320 € | 0.04 | 7.0 | 1.44 |
| EVENT V13.0 | 0.785 | 1.000 | **0.785** | 30 361 € | 0.04 | 9.0 | 2.40 |

## stress_cascade_nc_xl

| Doctrine | Q (quantity) | D (dispo SO) | OTIF | C (€) | Nervosité | WIP pic | WIP σ |
|---|---|---|---|---|---|---|---|
| OF | 0.938 | 1.000 | **0.938** | 33 745 € | 0.25 | 18.0 | 6.32 |
| OF+EVENT | 0.944 | 1.000 | **0.944** | 33 649 € | 0.05 | 18.0 | 6.32 |
| FLUX | 0.675 | 1.000 | **0.675** | 27 517 € | 0.25 | 7.0 | 1.38 |
| EVENT | 0.675 | 1.000 | **0.675** | 27 418 € | 0.05 | 7.0 | 1.38 |
| EVENT V13.0 | 0.950 | 1.000 | **0.950** | 33 368 € | 0.05 | 9.0 | 2.95 |

## stress_demand_spike_xl

| Doctrine | Q (quantity) | D (dispo SO) | OTIF | C (€) | Nervosité | WIP pic | WIP σ |
|---|---|---|---|---|---|---|---|
| OF | 0.885 | 1.000 | **0.885** | 51 009 € | 0.27 | 22.6 | 7.68 |
| OF+EVENT | 0.885 | 1.000 | **0.885** | 51 009 € | 0.09 | 22.6 | 7.68 |
| FLUX | 0.667 | 0.909 | **0.606** | 41 862 € | 0.27 | 18.6 | 4.19 |
| EVENT | 0.667 | 0.909 | **0.606** | 41 862 € | 0.09 | 18.6 | 4.19 |
| EVENT V13.0 | 0.667 | 0.909 | **0.606** | 41 862 € | 0.09 | 18.6 | 4.19 |

## Classements doctrinaux par objectif QCDS

Pour chaque scénario, doctrine gagnante par objectif. Q et D : plus haut = mieux. C et S (= 1/(1+wip_sd)) : plus bas wip_sd = plus stable = mieux.

| Scénario | Q max | C min (coût) | OTIF max | Stabilité max (WIP σ min) |
|---|---|---|---|---|
| baseline_xl | **OF** (0.950) | **FLUX** (31 679 €) | **OF** (0.950) | **FLUX** (σ 2.04) |
| stress_double_breakdown_xl | **OF** (0.950) | **EVENT** (27 320 €) | **OF** (0.950) | **FLUX** (σ 1.44) |
| stress_cascade_nc_xl | **EVENT V13.0** (0.950) | **EVENT** (27 418 €) | **EVENT V13.0** (0.950) | **FLUX** (σ 1.38) |
| stress_demand_spike_xl | **OF** (0.885) | **FLUX** (41 862 €) | **OF** (0.885) | **FLUX** (σ 4.19) |