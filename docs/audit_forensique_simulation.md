# Audit forensique de la simulation — analyse objective étape par étape

Audit demandé après la série V13 (qui a produit des résultats très
flatteurs : « FLUX+EVENT cyber-complet domine OF »). Objectif : vérifier
objectivement chaque étape et débusquer les erreurs de mesure ou de
modèle, **y compris dans mes propres ajouts récents V13.x**.

**Conclusion en une ligne** : aucun bug de code bloquant, mais
**deux erreurs de mesure sérieuses** qui ont conduit à surévaluer
l'apport doctrinal de FLUX/EVENT sur le Coût. Les conclusions
« FLUX est Cost-first » (§28.16) et « −30 à −42 % de coût » (§28.17)
sont **invalides ou fortement exagérées**.

---

## Finding 1 — OTIF=0.950 est un plafond mécanique, pas une mesure de qualité

Chaque OF clôturé a `qty_good ≈ 0.95 × quantity` (scrap fixe 5 %,
seul l'arrondi varie : 0.943-0.956). Conséquences :

- `quantity_compliance` ne peut **jamais dépasser ~0.95** dans un
  scénario sans rejet.
- « OTIF = 0.950 » signifie littéralement **« 100 % des OFs demandés
  ont été clôturés »**, pas « qualité quasi-parfaite ».
- Un OTIF < 0.95 signifie soit des OFs stuck (qty_good=0), soit des
  SOs rejetées (D < 1).

**Implication** : le KPI OTIF de ce modèle est essentiellement
**binaire au niveau OF** (clôturé → 0.95, stuck → 0), moyenné. Ce
n'est pas un bug, mais l'affirmation « parité 0.950 » doit se lire
« toutes les commandes produites », pas « excellence qualité ».

## Finding 2 — Jointure `quantity_compliance` : correcte (pas de double-compte)

Chaque SO mappe 3 candidates (article fini + 2 composants BOM) mais
le filtre `m.article_id = so.article_id` restreint `delivered` au
seul OF de l'article fini (`nmo=1`). Vérifié sur baseline et
demand_spike. **RAS.**

## Finding 3 — Les SOs rejetées sortent du dénominateur Q

`WHERE so.rejected_at IS NULL` : rejeter une commande inservable
**augmente Q** (on retire l'ordre difficile du dénominateur) tout en
**baissant D**. OTIF = Q × D capture le compromis, mais une doctrine
qui rejette agressivement gonfle son Q. Comportement intentionnel
(Point 2), à garder en tête pour l'interprétation.

## Finding 4 — ★ ERREUR DE MODÈLE : la MOD legacy facture 8 h par op

`costing/engine.py` : MOD = `durée_op × taux_horaire`, où durée_op =
`actual_end − actual_start`. En mode **legacy**, `_execute_op` tamponne
chaque op `0 → 480 min` (8 h **forfaitaire**), indépendamment de
`qty × unit_time`. En mode **réaliste** (V13.A), je tamponne la vraie
durée `qty × unit_time / capa`.

Conséquences graves :

1. **La MOD legacy est proportionnelle au NOMBRE d'ops exécutées**,
   pas au travail réel. ART-A qty=80 (≈120 min de travail) est facturé
   480 min comme ART-D qty=70.
2. **Un OF stuck (ops 3-4 jamais lancées) facture MOINS de MOD** :
   ne pas finir coûte moins cher. Incitation perverse.
3. La baisse « −38 % » du mode réaliste vs legacy est **à 95 % un
   artefact de facturation** (on facture enfin la MOD au temps réel),
   pas un gain doctrinal.

## Finding 5 — ★ « FLUX est Cost-first » est FAUX (au coût par unité)

Conséquence directe du Finding 4. Baseline legacy :

| Doctrine | Coût total | Unités livrées | **Coût / unité** |
|---|---|---|---|
| OF | 37 744 € | 533 | **70.8 €/u** |
| FLUX | 31 731 € | 390 | **81.4 €/u** |

Le coût total **inférieur** de FLUX vient de ce qu'il **produit moins**
(48 ops vs 53, 2 OFs stuck, 390 u vs 533). **Par unité livrée, FLUX
est 15 % PLUS CHER.** L'étude Option 1 (§28.16) qui titrait « FLUX
gagne le Coût 4/4 » comparait des **coûts totaux à volumes différents**
— métrique invalide.

Nuance : sur `stress_double_breakdown`, FLUX reste moins cher par
unité (85.3 vs 98.7 €/u) car OF concentre des ops longues pendant la
panne (facturées 8 h chacune). Là l'avantage est **partiellement
réel** (le lissage évite de concentrer le travail pendant la panne),
**partiellement artefact** (sous-production).

## Finding 6 — L'avantage V13 réel vs OF (même mode) est petit sur le coût, réel sur la stabilité

Comparaison **apples-to-apples** (les deux en mode réaliste, volumes
et OTIF identiques) :

| baseline | Coût/u | OTIF | Livré |
|---|---|---|---|
| EVENT V13 cyber+réal | 44.1 €/u | 0.950 | 530 |
| OF réaliste | 44.4 €/u | 0.950 | 530 |

À volume et OTIF égaux, EVENT n'est que **~1 % moins cher** que OF —
**pas −38 %**. Le « −38 % » de §28.17 comparait EVENT-réaliste contre
**OF-legacy** : il conflate (a) le correctif de facturation
legacy→réaliste et (b) l'écart doctrinal réel (~1-3 %).

**Ce qui survit** : la **Stabilité**. À volume et OTIF égaux, EVENT
V13 a un WIP σ ~25 % inférieur à OF (3.69 vs 4.95), grâce au lissage.
C'est le seul avantage doctrinal robuste de FLUX/EVENT une fois les
métriques corrigées.

---

## Vérifications passées (RAS)

| Test | Résultat |
|---|---|
| Déterminisme : scénario identique entre doctrines | ✅ offsets + SOs identiques |
| Jointure quantity_compliance | ✅ pas de double-compte |
| Material + scrap constants entre modes | ✅ 14392 / 1106 € invariants |
| pegging_links symétriques OF/FLUX/OF+E | ✅ 50 partout |
| Suite de tests | ✅ 488 passants |

## Corrections à porter au cadrage

1. **§28.16 Option 1** : « FLUX gagne le Coût 4/4 » → FAUX. Remplacer
   par « FLUX gagne le coût **total** par sous-production ; au coût
   **par unité livrée**, OF gagne baseline/cnc, FLUX ne gagne que les
   scénarios à panne sévère ».
2. **§28.17 V13.1** : « coût −30 à −42 % vs OF » → comparaison biaisée
   (legacy vs réaliste). Corriger en « à mode et volume égaux,
   l'avantage coût d'EVENT V13 vs OF est ~1-3 % ; l'avantage robuste
   est la Stabilité (WIP σ −25 %) ».
3. **Métrique de coût** : toujours rapporter **€/unité livrée**, jamais
   le coût total brut, pour comparer des doctrines à volumes différents.
4. **Modèle MOD** : documenter que la MOD legacy (8 h/op forfaitaire)
   surévalue le travail et crée une incitation perverse à
   l'incomplétude. Le mode réaliste (V13.A) est le modèle de coût
   correct ; les études legacy doivent être relues avec cette réserve.

## Verdict global

Le code ne contient **pas de bug de calcul**, mais le **modèle de coût
legacy** (MOD forfaitaire 8 h/op) combiné à la **comparaison de coûts
totaux à volumes différents** a produit deux conclusions erronées que
j'ai propagées dans §28.16 et §28.17. L'intuition de l'utilisateur
(« une erreur s'est probablement glissée ») était juste : ce n'est pas
un crash, c'est une **erreur de mesure** plus insidieuse.

Conclusion doctrinale corrigée : **FLUX/EVENT n'est pas moins cher que
OF** ; à output égal son seul avantage robuste est la **stabilité du
WIP** (−25 %). L'OTIF est plafonné à 0.95 pour tous par le modèle de
scrap. La supériorité « sur les 4 dimensions QCDS » annoncée en
§28.17 se réduit à **un avantage Stabilité réel + une parité Coût/OTIF**.
