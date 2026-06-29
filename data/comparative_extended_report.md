# Étude comparative étendue V5 — variance multi-seeds

**4 scénarios** × **4 doctrines** × **5 seeds** = 80 runs.

Seeds utilisées : `[42, 100, 200, 300, 400]`

Chaque scénario est rejoué avec un bruit déterministe (timing ±1 jour, magnitude ±20%) sur les aléas pour mesurer la stabilité doctrinale.

## Scénario `baseline`

| KPI | OF | FLUX | OF+EVENT | EVENT |
|---|---|---|---|---|
| Lead time moyen (j) | 2.95 ± 0.07 | 2.95 ± 0.07 | 2.75 ± 0.23 | 2.75 ± 0.23 |
| Lead time max (j) | 6.0 | 6.0 | 5.6 | 5.6 |
| WIP moyen | 1.17 ± 0.04 | 1.17 ± 0.04 | 1.06 ± 0.13 | 1.06 ± 0.13 |
| OF clôturés (moy.) | 8.0 | 8.0 | 8.0 | 8.0 |
| Recalculs APS (moy.) | 5.0 | 5.0 | 2.0 | 2.0 |
| Nervosité (replan/jour) | 0.333 | 0.333 | 0.133 | 0.133 |
| Écarts détectés (moy.) | 0.0 | 0.0 | 32.0 | 24.0 |
| Actions tolérance (moy.) | 0.0 | 0.0 | 32.0 | 24.0 |
| Replans globaux (moy.) | 0.0 | 0.0 | 0.0 | 0.0 |
| Causes attachées (moy.) | 0.0 | 0.0 | 96.0 | 72.0 |
| Événements qualité (moy.) | 0.0 | 2.0 | 0.0 | 2.0 |
| Coût total (€) | 18759 ± 1501 | 18759 ± 1501 | 10943 ± 1447 | 10943 ± 1447 |
| Coût par OF (€) | 2345 | 2345 | 1368 | 1368 |
| Coût scrap (€) | 333 | 333 | 380 | 380 |

## Scénario `stress_double_breakdown`

| KPI | OF | FLUX | OF+EVENT | EVENT |
|---|---|---|---|---|
| Lead time moyen (j) | 4.80 ± 0.18 | 4.80 ± 0.18 | 3.47 ± 0.19 | 3.47 ± 0.19 |
| Lead time max (j) | 7.0 | 7.0 | 5.0 | 5.0 |
| WIP moyen | 0.67 ± 0.00 | 0.67 ± 0.00 | 0.67 ± 0.00 | 0.67 ± 0.00 |
| OF clôturés (moy.) | 6.0 | 6.0 | 6.0 | 6.0 |
| Recalculs APS (moy.) | 3.0 | 3.0 | 1.0 | 1.0 |
| Nervosité (replan/jour) | 0.167 | 0.167 | 0.056 | 0.056 |
| Écarts détectés (moy.) | 0.0 | 0.0 | 24.0 | 24.0 |
| Actions tolérance (moy.) | 0.0 | 0.0 | 24.0 | 24.0 |
| Replans globaux (moy.) | 0.0 | 0.0 | 0.0 | 0.0 |
| Causes attachées (moy.) | 0.0 | 0.0 | 72.0 | 72.0 |
| Événements qualité (moy.) | 0.0 | 0.0 | 0.0 | 0.0 |
| Coût total (€) | 18326 ± 1196 | 18326 ± 1196 | 10401 ± 1025 | 10401 ± 1025 |
| Coût par OF (€) | 3054 | 3054 | 1733 | 1733 |
| Coût scrap (€) | 286 | 286 | 286 | 286 |

## Scénario `stress_cascade_nc`

| KPI | OF | FLUX | OF+EVENT | EVENT |
|---|---|---|---|---|
| Lead time moyen (j) | 3.00 ± 0.00 | 3.00 ± 0.00 | 3.00 ± 0.00 | 3.00 ± 0.00 |
| Lead time max (j) | 5.0 | 5.0 | 5.0 | 5.0 |
| WIP moyen | 0.80 ± 0.00 | 0.80 ± 0.00 | 0.80 ± 0.00 | 0.80 ± 0.00 |
| OF clôturés (moy.) | 6.0 | 6.0 | 6.0 | 6.0 |
| Recalculs APS (moy.) | 4.0 | 4.0 | 1.0 | 1.0 |
| Nervosité (replan/jour) | 0.267 | 0.267 | 0.067 | 0.067 |
| Écarts détectés (moy.) | 0.0 | 0.0 | 24.0 | 24.0 |
| Actions tolérance (moy.) | 0.0 | 0.0 | 24.0 | 24.0 |
| Replans globaux (moy.) | 0.0 | 0.0 | 0.0 | 0.0 |
| Causes attachées (moy.) | 0.0 | 0.0 | 72.0 | 72.0 |
| Événements qualité (moy.) | 0.0 | 6.0 | 0.0 | 6.0 |
| Coût total (€) | 7864 ± 134 | 7864 ± 134 | 7682 ± 80 | 7682 ± 80 |
| Coût par OF (€) | 1311 | 1311 | 1280 | 1280 |
| Coût scrap (€) | 682 | 682 | 500 | 500 |

## Scénario `stress_demand_spike`

| KPI | OF | FLUX | OF+EVENT | EVENT |
|---|---|---|---|---|
| Lead time moyen (j) | 2.67 ± 0.12 | 2.67 ± 0.12 | 2.67 ± 0.12 | 2.67 ± 0.12 |
| Lead time max (j) | 5.0 | 5.0 | 5.0 | 5.0 |
| WIP moyen | 1.73 ± 0.10 | 1.73 ± 0.10 | 1.73 ± 0.10 | 1.73 ± 0.10 |
| OF clôturés (moy.) | 12.0 | 12.0 | 12.0 | 12.0 |
| Recalculs APS (moy.) | 4.0 | 4.0 | 2.0 | 2.0 |
| Nervosité (replan/jour) | 0.267 | 0.267 | 0.133 | 0.133 |
| Écarts détectés (moy.) | 0.0 | 0.0 | 48.0 | 24.0 |
| Actions tolérance (moy.) | 0.0 | 0.0 | 48.0 | 24.0 |
| Replans globaux (moy.) | 0.0 | 0.0 | 0.0 | 0.0 |
| Causes attachées (moy.) | 0.0 | 0.0 | 144.0 | 72.0 |
| Événements qualité (moy.) | 0.0 | 0.0 | 0.0 | 0.0 |
| Coût total (€) | 12422 ± 44 | 12422 ± 44 | 12422 ± 44 | 12422 ± 44 |
| Coût par OF (€) | 1035 | 1035 | 1035 | 1035 |
| Coût scrap (€) | 380 | 380 | 380 | 380 |

## Lecture globale — décomposition 2×2 (flux × event sourcing)

Δ coût par doctrine vs OF (référence) :

| Scénario | OF (réf) | FLUX − OF (apport flux seul) | OF+EVENT − OF (apport event seul) | EVENT − OF (apport combiné) |
|---|---|---|---|---|
| baseline | 18759 € | +0 € | -7816 € | -7816 € |
| stress_double_breakdown | 18326 € | +0 € | -7925 € | -7925 € |
| stress_cascade_nc | 7864 € | +0 € | -182 € | -182 € |
| stress_demand_spike | 12422 € | +0 € | +0 € | +0 € |

**Lecture** : un Δ négatif signifie économie vs OF. La colonne « apport flux seul » mesure ce que la contractualisation flux apporte sans event sourcing ; la colonne « apport event seul » mesure ce que l'event sourcing apporte sans contractualisation. Si **flux seul ≈ 0** et **event seul ≈ event combiné**, on conclut que l'apport opérationnel réside dans l'event sourcing, pas dans la contractualisation.
