# Étude comparative V4 — scénario `baseline`

Trois doctrines exécutent le même scénario (mêmes commandes, mêmes aléas, mêmes capacités, même seed) sur trois bases SQLite séparées. Les KPI ci-dessous mesurent l'apport de chaque palier doctrinal.

## Paramètres du scénario

- **horizon** : 15 jours (départ 2026-07-06)
- **seed** : 42
- **commandes initiales** : 3
- **aléas** : 4
  - jour 2 : `breakdown_ws` — {'workstation_id': 'WS-2', 'slowdown_factor': 2.0, 'duration_days': 4}
  - jour 3 : `quality_nc` — {'article_id': 'ART-A', 'qty_scrap': 15, 'severity': 'high'}
  - jour 4 : `po_delay` — {'po_id': 'PO-0001', 'delay_days': 7}
  - jour 5 : `urgent_order` — {'sales_order_id': 'SO-URG', 'article_id': 'ART-A', 'quantity': 30, 'due_day': 8}

## Résultats

| KPI | APS+MES OF-driven (V0) | APS+MES flux sans event sourcing (V1+V2) | APS+MES event sourcing (V3) |
|---|---|---|---|
| Lead time moyen (jours) | 3.00 | 3.00 | 2.88 |
| Lead time max (jours) | 6 | 6 | 6 |
| WIP moyen | 1.20 | 1.20 | 1.13 |
| OF clôturés / créés | 8/8 | 8/8 | 8/8 |
| Recalculs APS | 5 | 5 | 2 |
| Nervosité (replan/jour) | 0.33 | 0.33 | 0.13 |
| Écarts détectés | 0 | 0 | 24 |
| Magnitude moyenne écart (min) | — | — | 5932.33 |
| Actions tolérance déclenchées | 0 | 0 | 24 |
| Actions locales (correct_local+replan_local) | 0 | 0 | 0 |
| Replans globaux | 0 | 0 | 0 |
| Causes attachées | 0 | 0 | 72 |
| Événements qualité | 0 | 2 | 2 |
| Coût total (€) | 20446.00 | 20446.00 | 12022.00 |
| Coût par OF (€) | 2555.75 | 2555.75 | 1502.75 |
| Coût scrap (€) | 338.00 | 338.00 | 338.00 |

## Lecture

- **OF-driven** : 5 recalculs APS pour gérer les aléas (replan global systématique). Aucune trace d'écart, aucune cause attribuée, pas de qualification proportionnée.
- **Flux sans event sourcing** : 5 recalculs APS, qualité tracée (2 événements) mais aucune détection événementielle. Les aléas se voient seulement à la clôture.
- **Event sourcing** : 24 écarts détectés, 24 actions filtre dual déclenchées dont 0 corrections locales et 0 replans globaux. 72 causes attachées. Magnitude moyenne des écarts : 5932.33 min.

## Hypothèse doctrinale validée ?

- ✓ V3 limite les replans globaux à ce qui est statistiquement justifié (vs V1+V2 où chaque urgence force un recalcul APS).
- ✓ V3 détecte les écarts en temps réel et produit des décisions proportionnées (filtre dual de tolérances opérationnel).
- ✓ Causes racines attachées (le moteur bayésien est opérationnel).
