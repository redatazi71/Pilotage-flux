# Étude comparative étendue V5 — variance multi-seeds

**3 scénarios** × **4 doctrines** × **3 seeds** = 36 runs.

Seeds utilisées : `[42, 100, 200]`

Chaque scénario est rejoué avec un bruit déterministe (timing ±1 jour, magnitude ±20%) sur les aléas pour mesurer la stabilité doctrinale.

## Scénario `baseline_xl`

| KPI | OF | FLUX | OF+EVENT | EVENT |
|---|---|---|---|---|
| Lead time moyen (j) | 8.62 ± 0.17 | 4.89 ± 0.18 | 8.38 ± 0.17 | 4.89 ± 0.18 |
| Lead time max (j) | 16.0 | 18.0 | 16.0 | 18.0 |
| WIP moyen | 8.15 ± 0.17 | 5.45 ± 0.17 | 7.90 ± 0.17 | 5.45 ± 0.17 |
| OF clôturés (moy.) | 21.0 | 19.0 | 21.0 | 19.0 |
| Recalculs APS (moy.) | 5.0 | 5.0 | 2.0 | 2.0 |
| Nervosité (replan/jour) | 0.250 | 0.250 | 0.100 | 0.100 |
| Écarts détectés (moy.) | 0.0 | 0.0 | 127.0 | 96.0 |
| Actions tolérance (moy.) | 0.0 | 0.0 | 127.0 | 96.0 |
| Replans globaux (moy.) | 0.0 | 0.0 | 0.0 | 0.0 |
| Causes attachées (moy.) | 0.0 | 0.0 | 381.0 | 288.0 |
| Événements qualité (moy.) | 0.0 | 2.0 | 0.0 | 2.0 |
| Coût total (€) | 44170 ± 2037 | 32090 ± 28 | 38450 ± 107 | 32090 ± 28 |
| Coût par OF (€) | 2103 | 1689 | 1831 | 1689 |
| Coût scrap (€) | 1151 | 922 | 1151 | 922 |

## Scénario `stress_double_breakdown_xl`

| KPI | OF | FLUX | OF+EVENT | EVENT |
|---|---|---|---|---|
| Lead time moyen (j) | 8.78 ± 0.06 | 4.92 ± 0.07 | 8.26 ± 0.06 | 4.88 ± 0.00 |
| Lead time max (j) | 16.0 | 19.0 | 16.0 | 19.0 |
| WIP moyen | 6.30 ± 0.03 | 4.36 ± 0.00 | 5.91 ± 0.00 | 4.36 ± 0.00 |
| OF clôturés (moy.) | 18.0 | 16.0 | 18.0 | 16.0 |
| Recalculs APS (moy.) | 3.0 | 3.0 | 1.0 | 1.0 |
| Nervosité (replan/jour) | 0.136 | 0.136 | 0.045 | 0.045 |
| Écarts détectés (moy.) | 0.0 | 0.0 | 108.0 | 100.0 |
| Actions tolérance (moy.) | 0.0 | 0.0 | 108.0 | 100.0 |
| Replans globaux (moy.) | 0.0 | 0.0 | 0.0 | 0.0 |
| Causes attachées (moy.) | 0.0 | 0.0 | 324.0 | 300.0 |
| Événements qualité (moy.) | 0.0 | 0.0 | 0.0 | 0.0 |
| Coût total (€) | 46884 ± 1513 | 28464 ± 1513 | 34778 ± 1513 | 27590 ± 0 |
| Coût par OF (€) | 2605 | 1779 | 1932 | 1724 |
| Coût scrap (€) | 1032 | 848 | 1032 | 848 |

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
| Coût total (€) | 21391 ± 0 | 9942 ± 0 | 19675 ± 0 | 9942 ± 0 |
| Coût par OF (€) | 2674 | 1988 | 2459 | 1988 |
| Coût scrap (€) | 949 | 469 | 949 | 469 |

## Lecture globale — décomposition 2×2 (flux × event sourcing)

Δ coût par doctrine vs OF (référence) :

| Scénario | OF (réf) | FLUX − OF (apport flux seul) | OF+EVENT − OF (apport event seul) | EVENT − OF (apport combiné) |
|---|---|---|---|---|
| baseline_xl | 44170 € | -12080 € | -5720 € | -12080 € |
| stress_double_breakdown_xl | 46884 € | -18420 € | -12106 € | -19294 € |
| stress_multi_contract_overload | 21391 € | -11449 € | -1716 € | -11449 € |

**Lecture** : un Δ négatif signifie économie vs OF. La colonne « apport flux seul » mesure ce que la contractualisation flux apporte sans event sourcing ; la colonne « apport event seul » mesure ce que l'event sourcing apporte sans contractualisation. Si **flux seul ≈ 0** et **event seul ≈ event combiné**, on conclut que l'apport opérationnel réside dans l'event sourcing, pas dans la contractualisation.
