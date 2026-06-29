# Étude comparative aléatoire (L10)

**2 fixture sets** × **2 scénarios** × **4 doctrines** = 16 runs

Seeds fixtures : `[1, 2]`
Seeds scénarios : `[100, 200]`

## Résultats agrégés par doctrine

| Doctrine | Lead time | WIP | Coût total | Recalc APS | Nervosité | Détections | Causes |
|---|---|---|---|---|---|---|---|
| OF | 9.30 ± 0.87 | 12.65 ± 1.86 | 120039 ± 30901 € | 6.0 | 0.333 | 0.0 | 0.0 |
| FLUX | 5.25 ± 0.34 | 7.16 ± 3.80 | 100978 ± 34325 € | 6.0 | 0.333 | 0.0 | 0.0 |
| OF_EVENT | 9.26 ± 0.84 | 12.59 ± 1.89 | 118002 ± 31673 € | 1.5 | 0.084 | 132.5 | 397.5 |
| EVENT | 5.00 ± 0.35 | 7.07 ± 3.90 | 95258 ± 35414 € | 1.5 | 0.084 | 89.5 | 268.5 |

## Décomposition 2×2 — Δ coût (€) vs OF

| | Flux ✗ | Flux ✓ |
|---|---|---|
| **Event ✗** | 0 (réf) | -19061 |
| **Event ✓** | -2037 | **-24781** |

Lecture : Δ négatif = économie vs OF. Coût OF = 120039 € sur l'ensemble du grid.
