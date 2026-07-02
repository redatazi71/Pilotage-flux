# Analyse statistique post-hoc — protocole master v2

Source : `ac83a1b1-master_v2_runs.csv` (1113 runs analysables)

Cette table applique les tests recommandés en review RFGI :
bootstrap IC 95 % sur les gains, Wilcoxon signed-rank paired
(même seed → même choc), Cliff's δ pour la taille d'effet.

Convention Cliff's δ : |δ| < 0.147 négligeable · < 0.33 petit · < 0.474 moyen · ≥ 0.474 grand.


## OF vs OF+EVENT

| Métrique | n | Moy. A | Moy. B | Δ (B−A) | IC 95 % Δ | Gain % | Wilcoxon p | Cliff δ | Effet |
|---|---:|---:|---:|---:|---:|---:|---:|---:|:---|
| otif | 370 | 0.946 | 0.946 | +0.000 | [-0.000, +0.000] | +0.01% | 0.826 | +0.008 | négligeable |
| cost_per_u | 370 | 113.255 | 110.979 | -2.276 | [-2.706, -1.877] | -2.01% | < 0.001 | +0.067 | négligeable |
| wip_sd | 370 | 6.182 | 6.183 | +0.000 | [-0.002, +0.003] | +0.01% | 0.146 | -0.001 | négligeable |
| nervousness | 370 | 0.133 | 0.047 | -0.086 | [-0.088, -0.085] | -65.00% | < 0.001 | +1.000 | grand |
| recovery_success_rate | 370 | 1.000 | 1.000 | +0.000 | [+0.000, +0.000] | +0.00% | n/a | +0.000 | négligeable |

## OF+EVENT vs FLUX+EVENT

| Métrique | n | Moy. A | Moy. B | Δ (B−A) | IC 95 % Δ | Gain % | Wilcoxon p | Cliff δ | Effet |
|---|---:|---:|---:|---:|---:|---:|---:|---:|:---|
| otif | 370 | 0.946 | 0.942 | -0.004 | [-0.007, -0.002] | -0.45% | 0.058 | +0.039 | négligeable |
| cost_per_u | 370 | 110.979 | 86.594 | -24.385 | [-26.096, -22.675] | -21.97% | < 0.001 | +0.607 | grand |
| wip_sd | 370 | 6.183 | 4.647 | -1.536 | [-1.607, -1.471] | -24.84% | < 0.001 | +0.553 | grand |
| nervousness | 370 | 0.047 | 0.037 | -0.010 | [-0.011, -0.009] | -21.36% | < 0.001 | +0.371 | moyen |
| recovery_success_rate | 370 | 1.000 | 0.998 | -0.002 | [-0.003, -0.001] | -0.18% | 0.023 | +0.016 | négligeable |

## OF vs FLUX+EVENT (cumul)

| Métrique | n | Moy. A | Moy. B | Δ (B−A) | IC 95 % Δ | Gain % | Wilcoxon p | Cliff δ | Effet |
|---|---:|---:|---:|---:|---:|---:|---:|---:|:---|
| otif | 370 | 0.946 | 0.942 | -0.004 | [-0.007, -0.002] | -0.44% | 0.074 | +0.047 | négligeable |
| cost_per_u | 370 | 113.255 | 86.594 | -26.661 | [-28.493, -24.902] | -23.54% | < 0.001 | +0.634 | grand |
| wip_sd | 370 | 6.182 | 4.647 | -1.535 | [-1.606, -1.469] | -24.83% | < 0.001 | +0.553 | grand |
| nervousness | 370 | 0.133 | 0.037 | -0.096 | [-0.098, -0.095] | -72.48% | < 0.001 | +1.000 | grand |
| recovery_success_rate | 370 | 1.000 | 0.998 | -0.002 | [-0.003, -0.001] | -0.18% | 0.023 | +0.016 | négligeable |
