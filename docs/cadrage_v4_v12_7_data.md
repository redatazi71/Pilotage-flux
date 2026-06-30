# V12.7 — Comparaison V11 vs V12.7 horizon-aware smoothing

## baseline_xl

| Doctrine | Régime | Q | D | OTIF | C (€) |
|---|---|---|---|---|---|
| FLUX | v11 | 0.693 | 1.000 | **0.693** | 31 679 € |
| FLUX | v12_7 | 0.950 | 1.000 | **0.950** | 41 022 € |
| EVENT | v11 | 0.693 | 1.000 | **0.693** | 31 679 € |
| EVENT | v12_7 | 0.950 | 1.000 | **0.950** | 37 590 € |

## stress_double_breakdown_xl

| Doctrine | Régime | Q | D | OTIF | C (€) |
|---|---|---|---|---|---|
| FLUX | v11 | 0.675 | 1.000 | **0.675** | 30 465 € |
| FLUX | v12_7 | 0.950 | 1.000 | **0.950** | 50 846 € |
| EVENT | v11 | 0.675 | 1.000 | **0.675** | 27 320 € |
| EVENT | v12_7 | 0.950 | 1.000 | **0.950** | 35 377 € |

## stress_cascade_nc_xl

| Doctrine | Régime | Q | D | OTIF | C (€) |
|---|---|---|---|---|---|
| FLUX | v11 | 0.675 | 1.000 | **0.675** | 27 518 € |
| FLUX | v12_7 | 0.950 | 1.000 | **0.950** | 33 542 € |
| EVENT | v11 | 0.675 | 1.000 | **0.675** | 27 419 € |
| EVENT | v12_7 | 0.950 | 1.000 | **0.950** | 33 458 € |

## stress_demand_spike_xl

| Doctrine | Régime | Q | D | OTIF | C (€) |
|---|---|---|---|---|---|
| FLUX | v11 | 0.678 | 0.909 | **0.616** | 42 247 € |
| FLUX | v12_7 | 0.872 | 0.900 | **0.784** | 50 886 € |
| EVENT | v11 | 0.678 | 0.909 | **0.616** | 42 247 € |
| EVENT | v12_7 | 0.872 | 0.900 | **0.784** | 50 886 € |

## Δ V12.7 vs V11 (gain OTIF, Δ coût)

| Scénario | Doctrine | Δ OTIF (pp) | Δ Coût (€) | Δ Coût (%) |
|---|---|---|---|---|
| baseline_xl | FLUX | **+25.6 pp** | +9 343 € | +29.5 % |
| baseline_xl | EVENT | **+25.6 pp** | +5 911 € | +18.7 % |
| stress_double_breakdown_xl | FLUX | **+27.5 pp** | +20 381 € | +66.9 % |
| stress_double_breakdown_xl | EVENT | **+27.5 pp** | +8 057 € | +29.5 % |
| stress_cascade_nc_xl | FLUX | **+27.5 pp** | +6 025 € | +21.9 % |
| stress_cascade_nc_xl | EVENT | **+27.5 pp** | +6 040 € | +22.0 % |
| stress_demand_spike_xl | FLUX | **+16.8 pp** | +8 638 € | +20.4 % |
| stress_demand_spike_xl | EVENT | **+16.8 pp** | +8 638 € | +20.4 % |