# Étude master consolidée — 5 configs × 4 stress × 10 seeds

Campagne propre post-fixes doctrinaux :
- Fix 1 : apply_cpm_absorption wireé dans OF+EVENT et FLUX+EVENT
- Fix 2 : 7 flags smoothing activés dans FLUX+EVENT

Total : 200 runs sequentiels, fixtures identiques entre configs.

## Stress FAIBLE (30j × 3 hazards)

| Config | OTIF | Q | D | €/u | WIP moy | WIP σ | Nervosité | Rupture % | Recovery j |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| OF | 0.950 | 0.950 | 1.000 | 108.99 | 3.47 | 5.06 | 0.133 | 0.0% | 6.5 |
| OF+EVENT | 0.950 | 0.950 | 1.000 | 104.72 | 3.43 | 5.07 | 0.047 | 0.0% | 6.3 |
| FLUX+EVENT | 0.925 | 0.948 | 0.975 | 89.35 | 2.70 | 3.96 | 0.047 | 2.5% | 5.5 |
| OF+EVENT+BCE | 0.950 | 0.950 | 1.000 | 104.72 | 3.43 | 5.07 | 0.047 | 0.0% | 6.3 |
| FLUX+EVENT+BCE | 0.925 | 0.948 | 0.975 | 89.35 | 2.70 | 3.96 | 0.047 | 2.5% | 5.5 |

**Écarts-types (σ inter-seeds)** — pour référence :

| Config | σ OTIF | σ €/u | σ Nervosité | σ Rupture |
|---|:-:|:-:|:-:|:-:|
| OF | 0.001 | 24.97 | 0.000 | 0.0% |
| OF+EVENT | 0.001 | 23.84 | 0.018 | 0.0% |
| FLUX+EVENT | 0.050 | 23.27 | 0.018 | 5.3% |
| OF+EVENT+BCE | 0.001 | 23.84 | 0.018 | 0.0% |
| FLUX+EVENT+BCE | 0.050 | 23.27 | 0.018 | 5.3% |

## Stress MOYEN (45j × 5 hazards)

| Config | OTIF | Q | D | €/u | WIP moy | WIP σ | Nervosité | Rupture % | Recovery j |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| OF | 0.947 | 0.947 | 1.000 | 123.33 | 5.50 | 8.43 | 0.133 | 0.0% | 14.3 |
| OF+EVENT | 0.947 | 0.947 | 1.000 | 120.38 | 5.42 | 8.42 | 0.040 | 0.0% | 13.9 |
| FLUX+EVENT | 0.949 | 0.949 | 1.000 | 112.51 | 4.86 | 6.88 | 0.040 | 0.0% | 14.7 |
| OF+EVENT+BCE | 0.947 | 0.947 | 1.000 | 120.38 | 5.42 | 8.42 | 0.040 | 0.0% | 13.9 |
| FLUX+EVENT+BCE | 0.949 | 0.949 | 1.000 | 112.51 | 4.86 | 6.88 | 0.040 | 0.0% | 14.7 |

**Écarts-types (σ inter-seeds)** — pour référence :

| Config | σ OTIF | σ €/u | σ Nervosité | σ Rupture |
|---|:-:|:-:|:-:|:-:|
| OF | 0.006 | 14.86 | 0.000 | 0.0% |
| OF+EVENT | 0.005 | 14.31 | 0.009 | 0.0% |
| FLUX+EVENT | 0.005 | 21.86 | 0.009 | 0.0% |
| OF+EVENT+BCE | 0.005 | 14.31 | 0.009 | 0.0% |
| FLUX+EVENT+BCE | 0.005 | 21.86 | 0.009 | 0.0% |

## Stress FORT (60j × 8 hazards)

| Config | OTIF | Q | D | €/u | WIP moy | WIP σ | Nervosité | Rupture % | Recovery j |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| OF | 0.944 | 0.944 | 1.000 | 114.91 | 5.44 | 9.61 | 0.150 | 0.0% | 17.0 |
| OF+EVENT | 0.946 | 0.946 | 1.000 | 112.19 | 5.42 | 9.60 | 0.031 | 0.0% | 16.8 |
| FLUX+EVENT | 0.943 | 0.943 | 1.000 | 110.27 | 4.38 | 7.17 | 0.031 | 0.0% | 14.6 |
| OF+EVENT+BCE | 0.946 | 0.946 | 1.000 | 111.97 | 5.42 | 9.60 | 0.031 | 0.0% | 16.8 |
| FLUX+EVENT+BCE | 0.945 | 0.945 | 1.000 | 109.71 | 4.38 | 7.17 | 0.031 | 0.0% | 14.6 |

**Écarts-types (σ inter-seeds)** — pour référence :

| Config | σ OTIF | σ €/u | σ Nervosité | σ Rupture |
|---|:-:|:-:|:-:|:-:|
| OF | 0.011 | 12.30 | 0.000 | 0.0% |
| OF+EVENT | 0.009 | 11.68 | 0.005 | 0.0% |
| FLUX+EVENT | 0.011 | 15.11 | 0.005 | 0.0% |
| OF+EVENT+BCE | 0.009 | 11.60 | 0.005 | 0.0% |
| FLUX+EVENT+BCE | 0.011 | 14.65 | 0.005 | 0.0% |

## Stress EXTRÊME (120j × 20 hazards)

| Config | OTIF | Q | D | €/u | WIP moy | WIP σ | Nervosité | Rupture % | Recovery j |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| OF | 0.939 | 0.945 | 0.993 | 118.09 | 7.25 | 15.04 | 0.175 | 0.7% | 20.0 |
| OF+EVENT | 0.941 | 0.948 | 0.993 | 113.72 | 7.21 | 15.04 | 0.016 | 0.7% | 20.0 |
| FLUX+EVENT | 0.944 | 0.947 | 0.997 | 111.29 | 6.15 | 11.67 | 0.016 | 0.3% | 19.7 |
| OF+EVENT+BCE | 0.941 | 0.948 | 0.993 | 113.61 | 7.21 | 15.04 | 0.016 | 0.7% | 20.0 |
| FLUX+EVENT+BCE | 0.944 | 0.947 | 0.997 | 110.94 | 6.15 | 11.67 | 0.016 | 0.3% | 19.7 |

**Écarts-types (σ inter-seeds)** — pour référence :

| Config | σ OTIF | σ €/u | σ Nervosité | σ Rupture |
|---|:-:|:-:|:-:|:-:|
| OF | 0.012 | 9.02 | 0.000 | 1.5% |
| OF+EVENT | 0.013 | 7.82 | 0.003 | 1.5% |
| FLUX+EVENT | 0.012 | 13.75 | 0.003 | 1.1% |
| OF+EVENT+BCE | 0.013 | 7.76 | 0.003 | 1.5% |
| FLUX+EVENT+BCE | 0.012 | 13.59 | 0.003 | 1.1% |

## Différentiels doctrinaux clés

### Stress faible

- **OF+EVENT vs OF** : ΔOTIF +0.000, Δ€/u -4.27, Δnervosité -0.086
- **FLUX+EVENT vs OF+EVENT** : ΔOTIF -0.025, Δ€/u -15.37, ΔWIP σ -1.11, Δrupture +2.5%
- **OF+EVENT+BCE vs OF+EVENT** : ΔOTIF +0.000, Δ€/u +0.00, Δnervosité +0.000
- **FLUX+EVENT+BCE vs FLUX+EVENT** : ΔOTIF +0.000, Δ€/u +0.00, Δnervosité +0.000

### Stress moyen

- **OF+EVENT vs OF** : ΔOTIF +0.001, Δ€/u -2.94, Δnervosité -0.093
- **FLUX+EVENT vs OF+EVENT** : ΔOTIF +0.002, Δ€/u -7.87, ΔWIP σ -1.54, Δrupture +0.0%
- **OF+EVENT+BCE vs OF+EVENT** : ΔOTIF +0.000, Δ€/u +0.00, Δnervosité +0.000
- **FLUX+EVENT+BCE vs FLUX+EVENT** : ΔOTIF +0.000, Δ€/u -0.00, Δnervosité +0.000

### Stress fort

- **OF+EVENT vs OF** : ΔOTIF +0.002, Δ€/u -2.71, Δnervosité -0.119
- **FLUX+EVENT vs OF+EVENT** : ΔOTIF -0.003, Δ€/u -1.92, ΔWIP σ -2.44, Δrupture +0.0%
- **OF+EVENT+BCE vs OF+EVENT** : ΔOTIF +0.000, Δ€/u -0.22, Δnervosité +0.000
- **FLUX+EVENT+BCE vs FLUX+EVENT** : ΔOTIF +0.001, Δ€/u -0.56, Δnervosité +0.000

### Stress extrême

- **OF+EVENT vs OF** : ΔOTIF +0.002, Δ€/u -4.37, Δnervosité -0.159
- **FLUX+EVENT vs OF+EVENT** : ΔOTIF +0.003, Δ€/u -2.43, ΔWIP σ -3.37, Δrupture -0.4%
- **OF+EVENT+BCE vs OF+EVENT** : ΔOTIF +0.000, Δ€/u -0.11, Δnervosité +0.000
- **FLUX+EVENT+BCE vs FLUX+EVENT** : ΔOTIF +0.000, Δ€/u -0.35, Δnervosité +0.000
