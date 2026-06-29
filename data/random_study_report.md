# Étude comparative aléatoire (L10)

**2 fixture sets** × **2 scénarios** × **4 doctrines** = 16 runs

Seeds fixtures : `[1, 2]`
Seeds scénarios : `[100, 200]`

## Résultats agrégés par doctrine

| Doctrine | Lead time | WIP | Coût total | Recalc APS | Nervosité | Détections | Causes |
|---|---|---|---|---|---|---|---|
| OF | 6.74 ± 0.74 | 8.72 ± 1.57 | 168030 ± 59548 € | 6.0 | 0.333 | 0.0 | 0.0 |
| FLUX | 3.96 ± 0.75 | 6.64 ± 1.33 | 142353 ± 54473 € | 6.0 | 0.333 | 0.0 | 0.0 |
| OF_EVENT | 6.74 ± 0.74 | 8.72 ± 1.57 | 167857 ± 59348 € | 2.0 | 0.111 | 149.2 | 447.8 |
| EVENT | 3.96 ± 0.75 | 6.64 ± 1.33 | 142192 ± 54497 € | 2.0 | 0.111 | 77.0 | 231.0 |

## Décomposition 2×2 — Δ coût (€) vs OF

| | Flux ✗ | Flux ✓ |
|---|---|---|
| **Event ✗** | 0 (réf) | -25677 |
| **Event ✓** | -173 | **-25838** |

Lecture : Δ négatif = économie vs OF. Coût OF = 168030 € sur l'ensemble du grid.
