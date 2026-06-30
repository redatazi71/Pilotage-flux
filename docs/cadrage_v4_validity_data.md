# Données brutes §7.1 + §7.3

## §7.1 — Validation OF_MILP

| Doctrine | N | Coût moyen | σ | Lead time | Δ vs OF |
|---|---|---|---|---|---|
| OF (SLACK+FIFO) | 50 | 130 089 € | 26 935 € | 7.72 j | +0 € |
| OF_MILP (CP-SAT) | 50 | 129 959 € | 26 756 € | 6.86 j | -131 € |
| FLUX | 50 | 95 261 € | 28 519 € | 4.63 j | -34 828 € |
| OF+EVENT | 50 | 123 241 € | 25 966 € | 7.58 j | -6 848 € |
| EVENT | 50 | 90 792 € | 28 166 € | 4.51 j | -39 297 € |

## §7.3 — Sensibilité 3 paramètres × 4 niveaux

### Coût scrap (multiplicateur)

| Niveau | OF | FLUX | OF+EVENT | EVENT |
|---|---|---|---|---|
| faible | 101 302 € | 74 292 € | 99 842 € | 73 697 € |
| moyen | 106 514 € | 82 827 € | 103 548 € | 80 758 € |
| élevé | 110 355 € | 93 402 € | 105 843 € | 88 688 € |
| extrême | 125 767 € | 112 026 € | 113 712 € | 102 970 € |

### Facteur tampon DBR (Little safety)

| Niveau | OF | FLUX | OF+EVENT | EVENT |
|---|---|---|---|---|
| faible | 83 544 € | 57 088 € | 82 874 € | 54 839 € |
| moyen | 104 917 € | 73 431 € | 101 778 € | 74 570 € |
| élevé | 109 750 € | 85 929 € | 105 085 € | 83 772 € |
| extrême | 104 473 € | 83 649 € | 102 521 € | 81 319 € |

### Nombre d'aléas par scénario

| Niveau | OF | FLUX | OF+EVENT | EVENT |
|---|---|---|---|---|
| faible | 101 334 € | 75 222 € | 100 614 € | 73 704 € |
| moyen | 109 750 € | 85 929 € | 105 085 € | 83 772 € |
| élevé | 119 586 € | 108 332 € | 112 416 € | 99 139 € |
| extrême | 127 622 € | 117 700 € | 116 993 € | 108 529 € |
