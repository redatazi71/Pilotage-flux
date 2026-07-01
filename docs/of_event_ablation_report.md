# Étude d'ablation — composants de la gestion événementielle (OF+EVENT)

Stress fort (60j × 8 hazards), 5 seeds par ablation.

## Tableau KPIs par ablation

| Ablation | OTIF | Q | D | €/u | WIP moy | WIP σ | Nervosité | Rupture % | Recovery j |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| baseline | 0.940 | 0.940 | 1.000 | 110.09 | 5.26 | 9.36 | 0.030 | 0.0% | 14.4 |
| +skip_latency_V13C | 0.940 | 0.940 | 1.000 | 110.09 | 5.26 | 9.36 | 0.030 | 0.0% | 14.4 |
| -CPM_absorption | 0.940 | 0.940 | 1.000 | 110.09 | 5.26 | 9.36 | 0.030 | 0.0% | 14.4 |
| -tolerance_filter | 0.939 | 0.939 | 1.000 | 111.53 | 5.28 | 9.36 | 0.030 | 0.0% | 14.8 |
| -all_filters | 0.939 | 0.939 | 1.000 | 111.53 | 5.28 | 9.36 | 0.030 | 0.0% | 14.8 |

## Contribution isolée par composant (vs baseline)

| Ablation | ΔOTIF | Δ€/u | ΔNervosité | Δrupture | Δrecovery |
|---|:-:|:-:|:-:|:-:|:-:|
| +skip_latency_V13C | +0.000 | +0.00 | +0.000 | +0.0% | +0.0j |
| -CPM_absorption | +0.000 | +0.00 | +0.000 | +0.0% | +0.0j |
| -tolerance_filter | -0.001 | +1.44 | +0.000 | +0.0% | +0.4j |
| -all_filters | -0.001 | +1.44 | +0.000 | +0.0% | +0.4j |