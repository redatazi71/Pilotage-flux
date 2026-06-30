# §30 — Étude OTIF-first sur 4 scénarios stress

## baseline_xl

| Doctrine | Q (compliance) | D (dispo) | OTIF = Q×D | C (coût €) |
|---|---|---|---|---|
| OF | 0.950 | 1.000 | **0.950** | 41 283 € |
| FLUX | 0.696 | 1.000 | **0.696** | 31 820 € |
| OF+EVENT | 0.950 | 1.000 | **0.950** | 37 851 € |
| EVENT | 0.696 | 1.000 | **0.696** | 31 820 € |

## stress_double_breakdown_xl

| Doctrine | Q (compliance) | D (dispo) | OTIF = Q×D | C (coût €) |
|---|---|---|---|---|
| OF | 0.950 | 1.000 | **0.950** | 47 706 € |
| FLUX | 0.675 | 1.000 | **0.675** | 30 203 € |
| OF+EVENT | 0.950 | 1.000 | **0.950** | 35 282 € |
| EVENT | 0.675 | 1.000 | **0.675** | 27 320 € |

## stress_cascade_nc_xl

| Doctrine | Q (compliance) | D (dispo) | OTIF = Q×D | C (coût €) |
|---|---|---|---|---|
| OF | 0.947 | 1.000 | **0.947** | 33 602 € |
| FLUX | 0.675 | 1.000 | **0.675** | 27 595 € |
| OF+EVENT | 0.948 | 1.000 | **0.948** | 33 559 € |
| EVENT | 0.675 | 1.000 | **0.675** | 27 458 € |

## stress_demand_spike_xl

| Doctrine | Q (compliance) | D (dispo) | OTIF = Q×D | C (coût €) |
|---|---|---|---|---|
| OF | 0.898 | 0.991 | **0.890** | 51 584 € |
| FLUX | 0.686 | 0.909 | **0.624** | 42 545 € |
| OF+EVENT | 0.898 | 0.991 | **0.890** | 51 584 € |
| EVENT | 0.686 | 0.909 | **0.624** | 42 545 € |

## Ranking OTIF-first (seuil = 95%)

| Scénario | Choix | OTIF | Coût | Alternative 2 |
|---|---|---|---|---|
| baseline_xl | **OF+EVENT** | 0.950 | 37 851 € | OF (OTIF 0.95  C 41 283 €) |
| stress_double_breakdown_xl | **OF+EVENT** | 0.950 | 35 282 € | OF (OTIF 0.95  C 47 706 €) |
| stress_cascade_nc_xl | **OF+EVENT** | 0.948 | 33 559 € | OF (OTIF 0.95  C 33 602 €) |
| stress_demand_spike_xl | **OF** | 0.890 | 51 584 € | OF+EVENT (OTIF 0.89  C 51 584 €) |

## Ranking OTIF-first (seuil = 90%)

| Scénario | Choix | OTIF | Coût | Alternative 2 |
|---|---|---|---|---|
| baseline_xl | **OF+EVENT** | 0.950 | 37 851 € | OF (OTIF 0.95  C 41 283 €) |
| stress_double_breakdown_xl | **OF+EVENT** | 0.950 | 35 282 € | OF (OTIF 0.95  C 47 706 €) |
| stress_cascade_nc_xl | **OF+EVENT** | 0.948 | 33 559 € | OF (OTIF 0.95  C 33 602 €) |
| stress_demand_spike_xl | **OF** | 0.890 | 51 584 € | OF+EVENT (OTIF 0.89  C 51 584 €) |

## Ranking OTIF-first (seuil = 80%)

| Scénario | Choix | OTIF | Coût | Alternative 2 |
|---|---|---|---|---|
| baseline_xl | **OF+EVENT** | 0.950 | 37 851 € | OF (OTIF 0.95  C 41 283 €) |
| stress_double_breakdown_xl | **OF+EVENT** | 0.950 | 35 282 € | OF (OTIF 0.95  C 47 706 €) |
| stress_cascade_nc_xl | **OF+EVENT** | 0.948 | 33 559 € | OF (OTIF 0.95  C 33 602 €) |
| stress_demand_spike_xl | **OF** | 0.890 | 51 584 € | OF+EVENT (OTIF 0.89  C 51 584 €) |
