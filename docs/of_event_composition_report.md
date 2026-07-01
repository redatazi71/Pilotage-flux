# Étude de composition incrémentale de la gestion événementielle

Stress fort (60j × 8 hazards), 5 seeds par configuration.

Chaque étape ajoute un composant à la précédente.

## Tableau KPIs par composition

| Étape | OTIF | Q | D | €/u | WIP moy | WIP σ | Nervosité | Rupture % | Recovery j |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| 1_OF_pur | 0.942 | 0.942 | 1.000 | 120.01 | 5.60 | 9.78 | 0.150 | 0.0% | 16.6 |
| 2_+capture_only | 0.942 | 0.942 | 1.000 | 120.01 | 5.60 | 9.78 | 0.030 | 0.0% | 16.6 |
| 3_+CPM_absorption | 0.942 | 0.942 | 1.000 | 120.01 | 5.60 | 9.78 | 0.030 | 0.0% | 16.6 |
| 4_+tolerance_filter | 0.946 | 0.946 | 1.000 | 115.04 | 5.54 | 9.76 | 0.030 | 0.0% | 16.4 |
| 5_+skip_latency_V13C | 0.946 | 0.946 | 1.000 | 115.04 | 5.54 | 9.76 | 0.030 | 0.0% | 16.4 |

## Gain marginal cumulé (vs étape précédente)

| Étape | ΔOTIF | Δ€/u | ΔNervosité | Δrupture | Δrecovery |
|---|:-:|:-:|:-:|:-:|:-:|
| 1_OF_pur | (référence) | | | | |
| 2_+capture_only | +0.000 | +0.00 | -0.120 | +0.0% | +0.0j |
| 3_+CPM_absorption | +0.000 | +0.00 | +0.000 | +0.0% | +0.0j |
| 4_+tolerance_filter | +0.004 | -4.97 | +0.000 | +0.0% | -0.2j |
| 5_+skip_latency_V13C | +0.000 | +0.00 | +0.000 | +0.0% | +0.0j |

## Gain cumulé (vs OF pur)

| Étape | ΔOTIF | Δ€/u | ΔNervosité | Δrupture | Δrecovery |
|---|:-:|:-:|:-:|:-:|:-:|
| 2_+capture_only | +0.000 | +0.00 | -0.120 | +0.0% | +0.0j |
| 3_+CPM_absorption | +0.000 | +0.00 | -0.120 | +0.0% | +0.0j |
| 4_+tolerance_filter | +0.004 | -4.97 | -0.120 | +0.0% | -0.2j |
| 5_+skip_latency_V13C | +0.004 | -4.97 | -0.120 | +0.0% | -0.2j |