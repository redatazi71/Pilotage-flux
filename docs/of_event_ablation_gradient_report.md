# Ablation OF+EVENT × gradient de stress (4 niveaux)

5 configurations d'ablation × 4 niveaux de stress × 5 seeds = 100 runs.

Objectif : tester si les briques nulles à stress fort le sont à tous les niveaux (CPM absorption, V13.C skip-latency).

## Stress FAIBLE

| Ablation | OTIF | €/u | WIP σ | Nervosité | Rupture % | Recovery j |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| baseline | 0.941 | 128.37 | 5.97 | 0.047 | 0.0% | 7.6 |
| +skip_latency_V13C | 0.941 | 128.37 | 5.97 | 0.047 | 0.0% | 7.6 |
| -CPM_absorption | 0.941 | 128.37 | 5.97 | 0.047 | 0.0% | 7.6 |
| -tolerance_filter | 0.941 | 130.95 | 5.95 | 0.047 | 0.0% | 7.6 |
| -all_filters | 0.941 | 130.95 | 5.95 | 0.047 | 0.0% | 7.6 |

Contribution isolée par composant (vs baseline) :

| Ablation | ΔOTIF | Δ€/u | ΔNervosité | Δrupture | Δrecovery |
|---|:-:|:-:|:-:|:-:|:-:|
| +skip_latency_V13C | +0.000 | +0.00 | +0.000 | +0.0% | +0.0j |
| -CPM_absorption | +0.000 | +0.00 | +0.000 | +0.0% | +0.0j |
| -tolerance_filter | +0.000 | +2.58 | +0.000 | +0.0% | +0.0j |
| -all_filters | +0.000 | +2.58 | +0.000 | +0.0% | +0.0j |

## Stress MOYEN

| Ablation | OTIF | €/u | WIP σ | Nervosité | Rupture % | Recovery j |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| baseline | 0.945 | 124.90 | 8.69 | 0.035 | 0.0% | 13.0 |
| +skip_latency_V13C | 0.945 | 124.90 | 8.69 | 0.035 | 0.0% | 13.0 |
| -CPM_absorption | 0.945 | 124.90 | 8.69 | 0.035 | 0.0% | 13.0 |
| -tolerance_filter | 0.945 | 127.93 | 8.70 | 0.035 | 0.0% | 13.2 |
| -all_filters | 0.945 | 127.93 | 8.70 | 0.035 | 0.0% | 13.2 |

Contribution isolée par composant (vs baseline) :

| Ablation | ΔOTIF | Δ€/u | ΔNervosité | Δrupture | Δrecovery |
|---|:-:|:-:|:-:|:-:|:-:|
| +skip_latency_V13C | +0.000 | +0.00 | +0.000 | +0.0% | +0.0j |
| -CPM_absorption | +0.000 | +0.00 | +0.000 | +0.0% | +0.0j |
| -tolerance_filter | +0.000 | +3.03 | +0.000 | +0.0% | +0.2j |
| -all_filters | +0.000 | +3.03 | +0.000 | +0.0% | +0.2j |

## Stress FORT

| Ablation | OTIF | €/u | WIP σ | Nervosité | Rupture % | Recovery j |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| baseline | 0.941 | 119.86 | 10.46 | 0.030 | 0.0% | 17.4 |
| +skip_latency_V13C | 0.941 | 119.86 | 10.46 | 0.030 | 0.0% | 17.4 |
| -CPM_absorption | 0.941 | 119.86 | 10.46 | 0.030 | 0.0% | 17.4 |
| -tolerance_filter | 0.937 | 121.96 | 10.45 | 0.030 | 0.0% | 17.2 |
| -all_filters | 0.937 | 121.96 | 10.45 | 0.030 | 0.0% | 17.2 |

Contribution isolée par composant (vs baseline) :

| Ablation | ΔOTIF | Δ€/u | ΔNervosité | Δrupture | Δrecovery |
|---|:-:|:-:|:-:|:-:|:-:|
| +skip_latency_V13C | +0.000 | +0.00 | +0.000 | +0.0% | +0.0j |
| -CPM_absorption | +0.000 | +0.00 | +0.000 | +0.0% | +0.0j |
| -tolerance_filter | -0.004 | +2.10 | +0.000 | +0.0% | -0.2j |
| -all_filters | -0.004 | +2.10 | +0.000 | +0.0% | -0.2j |

## Stress EXTRÊME

| Ablation | OTIF | €/u | WIP σ | Nervosité | Rupture % | Recovery j |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| baseline | 0.940 | 118.88 | 16.19 | 0.015 | 0.7% | 20.0 |
| +skip_latency_V13C | 0.940 | 118.88 | 16.19 | 0.015 | 0.7% | 20.0 |
| -CPM_absorption | 0.940 | 118.88 | 16.19 | 0.015 | 0.7% | 20.0 |
| -tolerance_filter | 0.938 | 122.32 | 16.21 | 0.015 | 0.7% | 20.0 |
| -all_filters | 0.938 | 122.32 | 16.21 | 0.015 | 0.7% | 20.0 |

Contribution isolée par composant (vs baseline) :

| Ablation | ΔOTIF | Δ€/u | ΔNervosité | Δrupture | Δrecovery |
|---|:-:|:-:|:-:|:-:|:-:|
| +skip_latency_V13C | +0.000 | +0.00 | +0.000 | +0.0% | +0.0j |
| -CPM_absorption | +0.000 | +0.00 | +0.000 | +0.0% | +0.0j |
| -tolerance_filter | -0.002 | +3.44 | +0.000 | +0.0% | +0.0j |
| -all_filters | -0.002 | +3.44 | +0.000 | +0.0% | +0.0j |

## Synthèse cross-niveaux

| Ablation | Δ€/u faible | Δ€/u moyen | Δ€/u fort | Δ€/u extrême |
|---|:-:|:-:|:-:|:-:|
| +skip_latency_V13C | +0.00 | +0.00 | +0.00 | +0.00 |
| -CPM_absorption | +0.00 | +0.00 | +0.00 | +0.00 |
| -tolerance_filter | +2.58 | +3.03 | +2.10 | +3.44 |
| -all_filters | +2.58 | +3.03 | +2.10 | +3.44 |

| Ablation | Δnerv faible | Δnerv moyen | Δnerv fort | Δnerv extrême |
|---|:-:|:-:|:-:|:-:|
| +skip_latency_V13C | +0.000 | +0.000 | +0.000 | +0.000 |
| -CPM_absorption | +0.000 | +0.000 | +0.000 | +0.000 |
| -tolerance_filter | +0.000 | +0.000 | +0.000 | +0.000 |
| -all_filters | +0.000 | +0.000 | +0.000 | +0.000 |