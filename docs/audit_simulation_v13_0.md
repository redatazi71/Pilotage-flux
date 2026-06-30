# Audit de la simulation — étape par étape

Demandé à la suite de la trouvaille matricielle V13.0 (OF+EVENT
domine FLUX+EVENT sur l'OTIF dans 3/4 scénarios) — investigation
des potentielles erreurs ou bugs silencieux dans le pipeline de
simulation `comparative/runner.py` + `comparative/kpis.py`.

## TL;DR

**Aucun bug évident détecté.** Les résultats inattendus
s'expliquent par **6 simplifications de modèle** qui composent ensemble
pour produire la matrice doctrinale observée. Aucune simplification
n'est doctrinalement asymétrique entre OF et FLUX (sauf #5 et #6 par
design L8.4).

## Findings (par ordre d'impact sur l'OTIF mesuré)

### Finding 1 — Sérialisation `1 op / WS / jour` (impact maximal)
`comparative/runner.py:970-972` dans `_advance_one_day` :
```python
if ws in busy_ws:
    continue
busy_ws.add(ws)
```
Un workstation ne traite **qu'une seule op par jour**, indépendamment
de la quantité. C'est la cause **structurelle** des OFs stuck en
FLUX : un OF avec 4 ops chaînées a besoin de 4 jours minimum, et si
lancé après day 16 dans un horizon de 20 jours, ne peut pas finir.

**Conséquence** : la capacité totale du système est ~6 WSs ×
horizon_days = ~120 ops max. La doctrine OF qui lance tout day 0
sature cette capacité dès le début. FLUX étale et laisse des OFs
sans capacité résiduelle.

Pas un bug, mais une simplification très impactante. Le modèle ne
reflète pas la capacité physique (où plusieurs OFs courts peuvent
passer dans un même jour).

### Finding 2 — Durée d'op fixée à 480 min (8h)
`comparative/runner.py:378-379` dans `_execute_op` :
```python
_stamp_event_at_day(conn, start_decl.event_id, horizon_start, day, 0)
_stamp_event_at_day(conn, finish_decl.event_id, horizon_start, day, 480)
```
Chaque op est tamponnée comme prenant exactement 8h, peu importe
`qty × unit_time`. Le module CPM (`aps/cpm_scheduling.py`) calcule des
makespans réalistes pour la planification — mais la simulation les
ignore au profit du modèle 1-op-par-jour.

**Conséquence** : la sensibilité au paramètre `quantity` est limitée
à la sortie qty_good ; pas à la durée d'exécution.

### Finding 3 — `qty_good = qty_good de la dernière op`
`mes/closure.py:61-63` :
```python
last_op = ops[-1]
qty_good = float(last_op["qty_good"] or 0.0)
```
Et dans `_advance_one_day`, chaque op calcule `qty_scrap = round(qty × 0.05)`
sur la **quantity de l'OF** (pas sur le qty_good de l'op précédente).
Le scrap n'est donc pas compoundé en cascade. Sortie finale
systématiquement ~95 % (sauf NC qui ajoutent du pending_scrap).

**Conséquence** : l'OTIF plafonne à 0.95 dans tous les scénarios
non-NC pour les doctrines OF/OF+EVENT.

### Finding 4 — V13.0 inactif sur stress_demand_spike
Vérifié dans la matrice : `stress_demand_spike_xl` a +0.0 pp pour
EVENT V13.0 vs EVENT V11. Ce scénario contient uniquement des
`HAZARD_URGENT_ORDER`, traités via `urgent_absorbed_no_aps_replan`
qui n'invoque PAS `_apply_corrective_actions`. Donc V13.0 n'a
aucun corrective physique à exploiter pour pull-forward.

**Pas un bug** : conséquence du design — V13.0 réagit aux
correctives physiques (breakdown_clear, qc_intervention,
po_alt_sourced), pas aux absorption d'urgent_orders.

### Finding 5 — `_apply_corrective_actions` n'est appelé QUE dans EVENT et OF+EVENT
Lignes 1555 (EVENT) et 1808 (OF+EVENT). OF et FLUX ne l'appellent
**jamais**. C'est cohérent avec L8.4 (FLUX = pas d'event sourcing).
Mais cela crée une asymétrie :
- `pending_nc_scrap` accumulé pour TOUTES les doctrines
- `qc_intervention_active` (qui halve pending) actif uniquement
  via `_apply_corrective_actions` → seuls EVENT et OF+EVENT en
  bénéficient

**Pas un bug doctrinal** mais cela contribue à l'écart cost/scrap
EVENT vs OF/FLUX sur scénarios à NC.

### Finding 6 — `open_nc + scrap_nc` doctrine-gated
`comparative/runner.py:474` :
```python
if doctrine in (DOCTRINE_FLUX, DOCTRINE_EVENT):
    # déclare open_nc + scrap_nc
```
OF et OF+EVENT ne déclarent PAS les NC dans le module qualité.
Cela affecte les KPIs `quality_events` mais pas la quantité
produite (les NC ajoutent `pending_nc_scrap` dans tous les cas).

## Vérifications de cohérence (PASSED)

| Test | Résultat |
|---|---|
| OF, FLUX, OF+EVENT créent 21 candidates et 50 pegging_links | ✅ Identique |
| candidates ont tous `sales_order_id != NULL` (pegging cascade) | ✅ Cohérent |
| `_of_blocked_by_pending_component` actif uniformément | ✅ Toutes doctrines |
| KPI quantity_compliance filtre `m.article_id = so.article_id` | ✅ Ne compte que les OFs sur article fini |
| Pas d'erreur silencieuse dans `_advance_one_day` | ✅ Logique uniforme |

## Bugs réels potentiels — aucun détecté

- Sérialisation WS, durée op, scrap : **simplifications de modèle**, pas bugs
- Asymétries OF vs FLUX/EVENT : **par design L8.4**
- V13.0 inactif sur stress_demand_spike : **conséquence logique**

## Hypothèses pour le finding doctrinal "OF+EVENT > FLUX+EVENT"

Dans le modèle actuel, OF+EVENT domine parce que :
1. OF+EVENT lance tout day 0 → saturation immédiate de la capacité
2. FLUX étale → certains OFs lancés trop tard pour leurs 4 ops
3. La sérialisation 1-op-par-WS-par-jour favorise structurellement
   le "lancer tout au plus tôt"

Si le modèle simulait une capacité **continue** (plusieurs OFs courts
dans un même jour) ou des **temps d'op réalistes** (qty × unit_time),
le smoothing FLUX pourrait s'avérer **bénéfique** : il éviterait la
saturation WS au début et permettrait un débit plus régulier.

**Pour valider cette hypothèse**, il faudrait soit :
- Modifier `_advance_one_day` pour autoriser N ops/WS/jour (où N
  dépend de la durée réelle des ops)
- OU mesurer l'impact sur les KPIs **C** et **S** (Cost et
  Stability) où FLUX devrait briller

## Recommandations

1. **Avant V13.1/V13.3** : mesurer C et S sur la matrice doctrinale
   (points 1 & 3 validés) pour confirmer que FLUX a une utilité
   ailleurs que l'OTIF.
2. **Considérer une simulation plus réaliste** : élargir la
   sérialisation pour autoriser plusieurs ops courtes par jour.
   Cela permettrait à FLUX de montrer son avantage sans toucher
   à la doctrine.
3. **Documenter ces simplifications** dans le cadrage §28 comme
   limitations connues du modèle de simulation (cf. §28 honnête
   sur V12.6 invalidé).

## Conclusion

L'audit ne révèle **aucun bug réel** dans le pipeline `runner.py +
kpis.py`. Les résultats matriciels V13.0 sont cohérents avec les
simplifications du modèle. La doctrine FLUX semble pénalisée sur
l'OTIF dans cette simulation parce que :
- La capacité est exprimée comme « 1 op / WS / jour » et la doctrine
  qui lance le plus tôt remporte mécaniquement.
- Cette simplification favorise OF/OF+EVENT (lance day 0) au
  détriment des doctrines de smoothing.

Pour que FLUX justifie sa préférence doctrinale, soit le modèle
doit être enrichi (cf. recommandation 2), soit l'évaluation doit
intégrer C et S au-delà de l'OTIF (cf. recommandation 1).
