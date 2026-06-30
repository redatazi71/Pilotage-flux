# Étude comparative étendue V5 — variance multi-seeds

**5 scénarios** × **4 doctrines** × **200 seeds** = 4000 runs.

Seeds utilisées : `[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 120, 121, 122, 123, 124, 125, 126, 127, 128, 129, 130, 131, 132, 133, 134, 135, 136, 137, 138, 139, 140, 141, 142, 143, 144, 145, 146, 147, 148, 149, 150, 151, 152, 153, 154, 155, 156, 157, 158, 159, 160, 161, 162, 163, 164, 165, 166, 167, 168, 169, 170, 171, 172, 173, 174, 175, 176, 177, 178, 179, 180, 181, 182, 183, 184, 185, 186, 187, 188, 189, 190, 191, 192, 193, 194, 195, 196, 197, 198, 199, 200]`

Chaque scénario est rejoué avec un bruit déterministe (timing ±1 jour, magnitude ±20%) sur les aléas pour mesurer la stabilité doctrinale.

## Scénario `baseline_xl`

| KPI | OF | FLUX | OF+EVENT | EVENT |
|---|---|---|---|---|
| Lead time moyen (j) | 8.65 ± 0.14 | 4.84 ± 0.12 | 8.34 ± 0.11 | 4.84 ± 0.12 |
| Lead time max (j) | 16.0 | 18.0 | 16.0 | 18.0 |
| WIP moyen | 8.18 ± 0.14 | 5.40 ± 0.11 | 7.86 ± 0.11 | 5.40 ± 0.11 |
| OF clôturés (moy.) | 21.0 | 19.0 | 21.0 | 19.0 |
| Recalculs APS (moy.) | 5.0 | 5.0 | 2.0 | 2.0 |
| Nervosité (replan/jour) | 0.250 | 0.250 | 0.100 | 0.100 |
| Écarts détectés (moy.) | 0.0 | 0.0 | 127.0 | 96.0 |
| Actions tolérance (moy.) | 0.0 | 0.0 | 127.0 | 96.0 |
| Replans globaux (moy.) | 0.0 | 0.0 | 0.0 | 0.0 |
| Causes attachées (moy.) | 0.0 | 0.0 | 381.0 | 288.0 |
| Événements qualité (moy.) | 0.0 | 2.0 | 0.0 | 2.0 |
| Coût total (€) | 45067 ± 893 | 32098 ± 140 | 39274 ± 1498 | 32098 ± 140 |
| Coût par OF (€) | 2146 | 1689 | 1870 | 1689 |
| Coût scrap (€) | 1176 | 961 | 1149 | 961 |

## Scénario `stress_double_breakdown_xl`

| KPI | OF | FLUX | OF+EVENT | EVENT |
|---|---|---|---|---|
| Lead time moyen (j) | 8.81 ± 0.06 | 5.00 ± 0.10 | 8.27 ± 0.05 | 4.88 ± 0.00 |
| Lead time max (j) | 16.0 | 19.0 | 16.0 | 19.0 |
| WIP moyen | 6.31 ± 0.03 | 4.36 ± 0.00 | 5.93 ± 0.02 | 4.36 ± 0.00 |
| OF clôturés (moy.) | 18.0 | 16.0 | 18.0 | 16.0 |
| Recalculs APS (moy.) | 3.0 | 3.0 | 1.0 | 1.0 |
| Nervosité (replan/jour) | 0.136 | 0.136 | 0.045 | 0.045 |
| Écarts détectés (moy.) | 0.0 | 0.0 | 108.0 | 100.0 |
| Actions tolérance (moy.) | 0.0 | 0.0 | 108.0 | 100.0 |
| Replans globaux (moy.) | 0.0 | 0.0 | 0.0 | 0.0 |
| Causes attachées (moy.) | 0.0 | 0.0 | 324.0 | 300.0 |
| Événements qualité (moy.) | 0.0 | 0.0 | 0.0 | 0.0 |
| Coût total (€) | 48586 ± 1688 | 30251 ± 2093 | 35890 ± 1721 | 27590 ± 0 |
| Coût par OF (€) | 2699 | 1891 | 1994 | 1724 |
| Coût scrap (€) | 1032 | 848 | 1032 | 848 |

## Scénario `stress_cascade_nc_xl`

| KPI | OF | FLUX | OF+EVENT | EVENT |
|---|---|---|---|---|
| Lead time moyen (j) | 8.22 ± 0.00 | 4.62 ± 0.00 | 8.22 ± 0.00 | 4.62 ± 0.00 |
| Lead time max (j) | 16.0 | 18.0 | 16.0 | 18.0 |
| WIP moyen | 6.50 ± 0.00 | 4.50 ± 0.00 | 6.50 ± 0.00 | 4.50 ± 0.00 |
| OF clôturés (moy.) | 18.0 | 16.0 | 18.0 | 16.0 |
| Recalculs APS (moy.) | 5.0 | 5.0 | 1.0 | 1.0 |
| Nervosité (replan/jour) | 0.250 | 0.250 | 0.050 | 0.050 |
| Écarts détectés (moy.) | 0.0 | 0.0 | 108.0 | 96.0 |
| Actions tolérance (moy.) | 0.0 | 0.0 | 108.0 | 96.0 |
| Replans globaux (moy.) | 0.0 | 0.0 | 0.0 | 0.0 |
| Causes attachées (moy.) | 0.0 | 0.0 | 324.0 | 288.0 |
| Événements qualité (moy.) | 0.0 | 6.5 | 0.0 | 6.5 |
| Coût total (€) | 34125 ± 234 | 27845 ± 112 | 34056 ± 138 | 27718 ± 56 |
| Coût par OF (€) | 1896 | 1740 | 1892 | 1732 |
| Coût scrap (€) | 1252 | 1103 | 1183 | 975 |

## Scénario `stress_demand_spike_xl`

| KPI | OF | FLUX | OF+EVENT | EVENT |
|---|---|---|---|---|
| Lead time moyen (j) | 10.19 ± 0.13 | 6.36 ± 0.25 | 10.19 ± 0.13 | 6.36 ± 0.25 |
| Lead time max (j) | 17.4 | 19.0 | 17.4 | 19.0 |
| WIP moyen | 15.41 ± 0.21 | 11.26 ± 0.61 | 15.41 ± 0.21 | 11.26 ± 0.61 |
| OF clôturés (moy.) | 29.1 | 27.2 | 29.1 | 27.2 |
| Recalculs APS (moy.) | 6.0 | 6.0 | 2.0 | 2.0 |
| Nervosité (replan/jour) | 0.273 | 0.273 | 0.091 | 0.091 |
| Écarts détectés (moy.) | 0.0 | 0.0 | 177.3 | 100.0 |
| Actions tolérance (moy.) | 0.0 | 0.0 | 177.3 | 100.0 |
| Replans globaux (moy.) | 0.0 | 0.0 | 0.0 | 0.0 |
| Causes attachées (moy.) | 0.0 | 0.0 | 531.8 | 300.0 |
| Événements qualité (moy.) | 0.0 | 0.0 | 0.0 | 0.0 |
| Coût total (€) | 47173 ± 548 | 41680 ± 1620 | 47173 ± 548 | 41680 ± 1620 |
| Coût par OF (€) | 1624 | 1532 | 1624 | 1532 |
| Coût scrap (€) | 1243 | 1067 | 1243 | 1067 |

## Scénario `stress_multi_contract_overload`

| KPI | OF | FLUX | OF+EVENT | EVENT |
|---|---|---|---|---|
| Lead time moyen (j) | 4.50 ± 0.00 | 2.00 ± 0.00 | 4.50 ± 0.00 | 2.00 ± 0.00 |
| Lead time max (j) | 7.0 | 3.0 | 7.0 | 3.0 |
| WIP moyen | 20.00 ± 0.00 | 10.14 ± 0.00 | 20.00 ± 0.00 | 10.14 ± 0.00 |
| OF clôturés (moy.) | 8.0 | 5.0 | 8.0 | 5.0 |
| Recalculs APS (moy.) | 2.0 | 2.0 | 1.0 | 1.0 |
| Nervosité (replan/jour) | 0.286 | 0.286 | 0.143 | 0.143 |
| Écarts détectés (moy.) | 0.0 | 0.0 | 54.0 | 43.0 |
| Actions tolérance (moy.) | 0.0 | 0.0 | 54.0 | 43.0 |
| Replans globaux (moy.) | 0.0 | 0.0 | 0.0 | 0.0 |
| Causes attachées (moy.) | 0.0 | 0.0 | 162.0 | 129.0 |
| Événements qualité (moy.) | 0.0 | 0.0 | 0.0 | 0.0 |
| Coût total (€) | 21391 ± 0 | 9942 ± 0 | 20104 ± 745 | 9942 ± 0 |
| Coût par OF (€) | 2674 | 1988 | 2513 | 1988 |
| Coût scrap (€) | 949 | 469 | 949 | 469 |

## Lecture globale — décomposition 2×2 (flux × event sourcing)

Δ coût par doctrine vs OF (référence) :

| Scénario | OF (réf) | FLUX − OF (apport flux seul) | OF+EVENT − OF (apport event seul) | EVENT − OF (apport combiné) |
|---|---|---|---|---|
| baseline_xl | 45067 € | -12969 € | -5793 € | -12969 € |
| stress_double_breakdown_xl | 48586 € | -18336 € | -12697 € | -20996 € |
| stress_cascade_nc_xl | 34125 € | -6280 € | -69 € | -6407 € |
| stress_demand_spike_xl | 47173 € | -5492 € | +0 € | -5492 € |
| stress_multi_contract_overload | 21391 € | -11449 € | -1287 € | -11449 € |

**Lecture** : un Δ négatif signifie économie vs OF. La colonne « apport flux seul » mesure ce que la contractualisation flux apporte sans event sourcing ; la colonne « apport event seul » mesure ce que l'event sourcing apporte sans contractualisation. Si **flux seul ≈ 0** et **event seul ≈ event combiné**, on conclut que l'apport opérationnel réside dans l'event sourcing, pas dans la contractualisation.
