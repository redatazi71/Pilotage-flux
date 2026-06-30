"""Analyse de résilience des 4 doctrines (§24.8 cadrage v4).

Ce module comble les 5 manques identifiés pour parler de **résilience**
au sens technique :

  1. Distributions de coût brutes (P50/P75/P95/P99) — pas seulement moyenne±std
  2. Time-to-recover après choc — exploitation de daily_wip
  3. Gradient performance vs intensité d'aléa
  4. Cascade de défaillances simultanées (1..5 pannes)
  5. Proxy MTTR / MTBF inspiré sûreté de fonctionnement

À la différence de `variance.aggregate_kpis`, ces fonctions **conservent
les échantillons bruts** pour les statistiques d'ordre.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path

from pilotage_flux.comparative.kpis import KpiSet, compute_kpis
from pilotage_flux.comparative.random_scenario import (
    RandomScenarioSpec,
    generate_random_scenario,
)
from pilotage_flux.comparative.runner import RunResult, run_doctrine
from pilotage_flux.comparative.scenario import (
    HAZARD_BREAKDOWN,
    HAZARD_LOGISTIC_DELAY,
    HAZARD_PO_DELAY,
    HAZARD_QUALITY_NC,
    HAZARD_URGENT_ORDER,
    HazardEvent,
    Scenario,
)
from pilotage_flux.data_factory import (
    DEFAULT_SPEC,
    FixtureSpec,
    generate_random_fixtures,
)


# ---------------------------------------------------------------------------
# 1) Distributions de coût + percentiles
# ---------------------------------------------------------------------------

@dataclass
class CostDistribution:
    """Distribution complète de coût pour 1 doctrine sur N runs."""

    doctrine: str
    n_runs: int
    samples: list[float]
    mean: float
    std: float
    p50: float
    p75: float
    p90: float
    p95: float
    p99: float
    min_val: float
    max_val: float


def _percentile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = (len(s) - 1) * q
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return float(s[f])
    return float(s[f] + (s[c] - s[f]) * (k - f))


def compute_cost_distribution(
    doctrine: str, kpis: list[KpiSet]
) -> CostDistribution:
    samples = [k.total_cost_eur for k in kpis]
    if not samples:
        return CostDistribution(doctrine, 0, [], 0, 0, 0, 0, 0, 0, 0, 0, 0)
    return CostDistribution(
        doctrine=doctrine,
        n_runs=len(samples),
        samples=samples,
        mean=statistics.mean(samples),
        std=statistics.stdev(samples) if len(samples) > 1 else 0.0,
        p50=_percentile(samples, 0.50),
        p75=_percentile(samples, 0.75),
        p90=_percentile(samples, 0.90),
        p95=_percentile(samples, 0.95),
        p99=_percentile(samples, 0.99),
        min_val=min(samples),
        max_val=max(samples),
    )


# ---------------------------------------------------------------------------
# 2) Time-to-recover (MTTR) à partir de daily_wip
# ---------------------------------------------------------------------------

def compute_time_to_recover(
    result: RunResult,
    shock_day: int,
    tolerance: float = 0.30,
    max_recovery_days: int = 20,
) -> int:
    """Mesure le délai (en jours) entre le pic post-choc et le retour
    sous `(1 + tolerance) × médiane(daily_wip)` du run.

    La médiane sur le run entier sert de baseline « régime normal » —
    plus robuste que la moyenne pré-choc car la prod n'est pas encore
    montée dans les premiers jours.

    Retourne `max_recovery_days` si pas de retour avant cette borne.
    """
    if not result.daily_wip:
        return max_recovery_days
    median_wip = statistics.median(result.daily_wip.values())
    threshold = median_wip * (1.0 + tolerance)

    # Pic post-choc dans la fenêtre [shock_day, shock_day + max_recovery_days]
    candidates = {
        d: result.daily_wip[d]
        for d in range(shock_day, shock_day + max_recovery_days + 1)
        if d in result.daily_wip
    }
    if not candidates:
        return max_recovery_days
    peak_day = max(candidates, key=lambda d: candidates[d])
    # Si pas de surcharge réelle, recovery = 0
    if candidates[peak_day] <= threshold:
        return 0
    # Compter les jours depuis le pic jusqu'au retour sous threshold
    for d in range(peak_day, peak_day + max_recovery_days + 1):
        if result.daily_wip.get(d, 0) <= threshold:
            return d - shock_day
    return max_recovery_days


# ---------------------------------------------------------------------------
# 3) Gradient d'intensité — N seeds × N doctrines × N niveaux d'intensité
# ---------------------------------------------------------------------------

@dataclass
class IntensityPoint:
    """1 point de la courbe gradient (1 doctrine à 1 intensité)."""

    doctrine: str
    intensity_factor: float
    n_runs: int
    cost_mean: float
    cost_p95: float
    lead_time_mean: float


@dataclass
class CascadePoint:
    """1 point de la courbe cascade (1 doctrine à N pannes simultanées)."""

    doctrine: str
    n_breakdowns: int
    n_runs: int
    cost_mean: float
    cost_p95: float
    lead_time_mean: float
    recovery_days_mean: float
    availability_mean: float = 0.0  # % OF clôturés / créés (disponibilité)


@dataclass
class PairedHazardPoint:
    """§24.10 — Impact d'une paire d'aléas simultanés sur 1 doctrine."""

    doctrine: str
    domain_a: str  # appro | logi | qual | prod | dem
    domain_b: str
    n_runs: int
    cost_mean: float
    cost_baseline_mean: float  # 1 seul aléa = baseline
    amplification: float  # cost_mean / cost_baseline_mean


# Map domaine → kind hazard
DOMAIN_TO_HAZARD = {
    "appro": HAZARD_PO_DELAY,
    "logi": HAZARD_LOGISTIC_DELAY,
    "qual": HAZARD_QUALITY_NC,
    "prod": HAZARD_BREAKDOWN,
    "dem": HAZARD_URGENT_ORDER,
}
DOMAINS = ["appro", "logi", "qual", "prod", "dem"]


def _build_intensity_scenario(
    base_seed: int,
    intensity: float,
    fixtures_dir: Path,
) -> Scenario:
    """Génère un scénario avec aléas amplifiés par `intensity`.

    intensity=1.0 = scénario normal. intensity=2.0 = aléas 2× plus durs.
    """
    spec = RandomScenarioSpec(
        n_hazards=5,
        breakdown_duration_range=(
            max(1, int(2 * intensity)), max(2, int(5 * intensity))
        ),
        breakdown_factor_range=(1.5 * intensity, 3.0 * intensity),
        nc_scrap_range=(
            max(1, int(10 * intensity)), max(2, int(25 * intensity))
        ),
        po_delay_range=(
            max(1, int(3 * intensity)), max(2, int(10 * intensity))
        ),
    )
    return generate_random_scenario(spec, seed=base_seed, fixtures_dir=fixtures_dir)


def _build_cascade_scenario(
    base_seed: int,
    n_breakdowns: int,
    fixtures_dir: Path,
) -> Scenario:
    """Génère un scénario avec N pannes **simultanées** au jour 3.

    Mesure la robustesse face à des défaillances en cascade.
    """
    import csv
    import random as _r

    rng = _r.Random(base_seed)
    workstations = []
    with (fixtures_dir / "workstations.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            workstations.append(row["workstation_id"])
    if len(workstations) < n_breakdowns:
        n_breakdowns = len(workstations)

    # Scénario de base sans aléas
    base = generate_random_scenario(
        RandomScenarioSpec(n_hazards=0),
        seed=base_seed,
        fixtures_dir=fixtures_dir,
    )
    # Injecte N pannes simultanées sur N postes différents au jour 3
    targets = rng.sample(workstations, n_breakdowns)
    hazards: list[HazardEvent] = []
    for ws in targets:
        hazards.append(HazardEvent(
            day=3, kind=HAZARD_BREAKDOWN,
            payload={
                "workstation_id": ws,
                "slowdown_factor": 2.5,
                "duration_days": 3,
            },
        ))
    return Scenario(
        name=f"cascade_{n_breakdowns}_seed{base_seed}",
        seed=base_seed,
        horizon_days=base.horizon_days,
        horizon_start=base.horizon_start,
        initial_sales_orders=base.initial_sales_orders,
        initial_stocks=base.initial_stocks,
        initial_purchase_orders=base.initial_purchase_orders,
        hazards=hazards,
    )


# ---------------------------------------------------------------------------
# 4) Orchestrateurs de grids résilience
# ---------------------------------------------------------------------------

@dataclass
class ResilienceStudy:
    """Étude résilience complète : distributions + gradient + cascade."""

    distributions: dict[str, CostDistribution] = field(default_factory=dict)
    intensity_curve: list[IntensityPoint] = field(default_factory=list)
    cascade_curve: list[CascadePoint] = field(default_factory=list)
    mttr_per_doctrine: dict[str, float] = field(default_factory=dict)
    raw_kpis: dict[str, list[KpiSet]] = field(default_factory=dict)


def run_distribution_grid(
    *,
    fixture_seeds: list[int],
    scenario_seeds: list[int],
    doctrines: list[str],
    work_dir: Path,
    fixture_spec: FixtureSpec | None = None,
    on_run_complete=None,
) -> dict[str, list[KpiSet]]:
    """Lance un grid (fixtures × scénarios × doctrines) et **conserve**
    tous les KpiSet bruts pour les statistiques d'ordre."""
    if fixture_spec is None:
        fixture_spec = DEFAULT_SPEC
    work_dir.mkdir(parents=True, exist_ok=True)
    raw: dict[str, list[KpiSet]] = {d: [] for d in doctrines}
    for fix_seed in fixture_seeds:
        fix_dir = work_dir / f"fixtures_{fix_seed}"
        generate_random_fixtures(fixture_spec, seed=fix_seed, out_dir=fix_dir)
        for scen_seed in scenario_seeds:
            scen = generate_random_scenario(
                RandomScenarioSpec(), seed=scen_seed, fixtures_dir=fix_dir,
            )
            for d in doctrines:
                db_path = work_dir / f"run_{fix_seed}_{scen_seed}_{d}.db"
                result = run_doctrine(scen, d, db_path, fixtures_dir=fix_dir)
                raw[d].append(compute_kpis(scen, result))
                if on_run_complete is not None:
                    on_run_complete(fix_seed, scen_seed, d)
    return raw


def run_intensity_curve(
    *,
    intensities: list[float],
    seeds: list[int],
    doctrines: list[str],
    fixtures_dir: Path,
    work_dir: Path,
    on_run_complete=None,
) -> list[IntensityPoint]:
    """Pour chaque intensité, exécute `seeds` × `doctrines` runs."""
    work_dir.mkdir(parents=True, exist_ok=True)
    points: list[IntensityPoint] = []
    for intensity in intensities:
        per_doctrine: dict[str, list[KpiSet]] = {d: [] for d in doctrines}
        for seed in seeds:
            scen = _build_intensity_scenario(seed, intensity, fixtures_dir)
            for d in doctrines:
                db_path = work_dir / f"int_{intensity}_{seed}_{d}.db"
                result = run_doctrine(scen, d, db_path, fixtures_dir=fixtures_dir)
                per_doctrine[d].append(compute_kpis(scen, result))
                if on_run_complete is not None:
                    on_run_complete(intensity, seed, d)
        for d in doctrines:
            costs = [k.total_cost_eur for k in per_doctrine[d]]
            lts = [k.lead_time_days_avg for k in per_doctrine[d]]
            points.append(IntensityPoint(
                doctrine=d,
                intensity_factor=intensity,
                n_runs=len(costs),
                cost_mean=statistics.mean(costs) if costs else 0,
                cost_p95=_percentile(costs, 0.95),
                lead_time_mean=statistics.mean(lts) if lts else 0,
            ))
    return points


def run_cascade_curve(
    *,
    n_breakdowns_list: list[int],
    seeds: list[int],
    doctrines: list[str],
    fixtures_dir: Path,
    work_dir: Path,
    on_run_complete=None,
) -> list[CascadePoint]:
    """Pour chaque niveau de cascade, exécute `seeds` × `doctrines` runs."""
    work_dir.mkdir(parents=True, exist_ok=True)
    points: list[CascadePoint] = []
    for n_bd in n_breakdowns_list:
        per_doctrine: dict[str, list[tuple[KpiSet, RunResult]]] = {
            d: [] for d in doctrines
        }
        for seed in seeds:
            scen = _build_cascade_scenario(seed, n_bd, fixtures_dir)
            for d in doctrines:
                db_path = work_dir / f"casc_{n_bd}_{seed}_{d}.db"
                result = run_doctrine(scen, d, db_path, fixtures_dir=fixtures_dir)
                per_doctrine[d].append((compute_kpis(scen, result), result))
                if on_run_complete is not None:
                    on_run_complete(n_bd, seed, d)
        for d in doctrines:
            costs = [k.total_cost_eur for k, _ in per_doctrine[d]]
            lts = [k.lead_time_days_avg for k, _ in per_doctrine[d]]
            recs = [
                compute_time_to_recover(r, shock_day=3)
                for _, r in per_doctrine[d]
            ]
            # Disponibilité = OF clôturés / OF créés
            avail = [
                (k.of_closed / k.of_total) if k.of_total > 0 else 0.0
                for k, _ in per_doctrine[d]
            ]
            points.append(CascadePoint(
                doctrine=d,
                n_breakdowns=n_bd,
                n_runs=len(costs),
                cost_mean=statistics.mean(costs) if costs else 0,
                cost_p95=_percentile(costs, 0.95),
                lead_time_mean=statistics.mean(lts) if lts else 0,
                recovery_days_mean=statistics.mean(recs) if recs else 0,
                availability_mean=statistics.mean(avail) if avail else 0,
            ))
    return points


# ---------------------------------------------------------------------------
# 5) §24.10 — Matrice de paires de domaines
# ---------------------------------------------------------------------------

def _build_hazard(
    rng_local,
    kind: str,
    day: int,
    fixtures_dir: Path,
) -> HazardEvent | None:
    """Construit un aléa unique du domaine demandé."""
    import csv
    workstations = []
    finished = []
    semis = []
    components = []
    with (fixtures_dir / "workstations.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            workstations.append(row["workstation_id"])
    with (fixtures_dir / "articles.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            aid = row["article_id"]
            is_purchased = int(row.get("is_purchased") or 0) == 1
            if is_purchased:
                components.append(aid)
            elif aid.startswith("SEMI"):
                semis.append(aid)
            else:
                finished.append(aid)

    if kind == HAZARD_BREAKDOWN:
        return HazardEvent(day=day, kind=kind, payload={
            "workstation_id": rng_local.choice(workstations),
            "slowdown_factor": 2.5,
            "duration_days": 3,
        })
    if kind == HAZARD_QUALITY_NC:
        targets = finished + semis
        if not targets:
            return None
        return HazardEvent(day=day, kind=kind, payload={
            "article_id": rng_local.choice(targets),
            "qty_scrap": 20,
            "severity": "high",
        })
    if kind == HAZARD_PO_DELAY:
        # Cible un PO virtuel ; en pratique, fragmentation des composants
        if not components:
            return None
        return HazardEvent(day=day, kind=kind, payload={
            "po_id": "PO-0001",
            "delay_days": 5,
        })
    if kind == HAZARD_URGENT_ORDER:
        if not finished:
            return None
        # ID unique pour éviter conflit en cellule diagonale dem-dem
        suffix = rng_local.randint(10000, 99999)
        return HazardEvent(day=day, kind=kind, payload={
            "sales_order_id": f"SO-URG-PAIR-{day}-{suffix}",
            "article_id": rng_local.choice(finished),
            "quantity": 40,
            "due_day": day + 7,
        })
    if kind == HAZARD_LOGISTIC_DELAY:
        return HazardEvent(day=day, kind=kind, payload={
            "workstation_id": rng_local.choice(workstations),
            "block_days": 3,
        })
    return None


def _build_paired_scenario(
    base_seed: int,
    domain_a: str,
    domain_b: str,
    fixtures_dir: Path,
) -> Scenario:
    """Génère un scénario avec 1 aléa de chaque domaine au jour 3.

    Si domain_a == domain_b, injecte 2 aléas du même type pour mesurer
    la sensibilité doctrinale à 2 chocs homogènes (cellule diagonale).
    """
    import random as _r
    rng = _r.Random(base_seed)

    base = generate_random_scenario(
        RandomScenarioSpec(n_hazards=0),
        seed=base_seed,
        fixtures_dir=fixtures_dir,
    )
    kind_a = DOMAIN_TO_HAZARD[domain_a]
    kind_b = DOMAIN_TO_HAZARD[domain_b]
    hazards = []
    h1 = _build_hazard(rng, kind_a, day=3, fixtures_dir=fixtures_dir)
    if h1 is not None:
        hazards.append(h1)
    h2 = _build_hazard(rng, kind_b, day=3, fixtures_dir=fixtures_dir)
    if h2 is not None:
        hazards.append(h2)
    return Scenario(
        name=f"pair_{domain_a}_{domain_b}_seed{base_seed}",
        seed=base_seed,
        horizon_days=base.horizon_days,
        horizon_start=base.horizon_start,
        initial_sales_orders=base.initial_sales_orders,
        initial_stocks=base.initial_stocks,
        initial_purchase_orders=base.initial_purchase_orders,
        hazards=hazards,
    )


def _build_single_domain_scenario(
    base_seed: int,
    domain: str,
    fixtures_dir: Path,
) -> Scenario:
    """Scénario avec 1 seul aléa du domaine — baseline pour amplification."""
    import random as _r
    rng = _r.Random(base_seed)
    base = generate_random_scenario(
        RandomScenarioSpec(n_hazards=0),
        seed=base_seed,
        fixtures_dir=fixtures_dir,
    )
    h = _build_hazard(rng, DOMAIN_TO_HAZARD[domain], day=3,
                      fixtures_dir=fixtures_dir)
    return Scenario(
        name=f"single_{domain}_seed{base_seed}",
        seed=base_seed,
        horizon_days=base.horizon_days,
        horizon_start=base.horizon_start,
        initial_sales_orders=base.initial_sales_orders,
        initial_stocks=base.initial_stocks,
        initial_purchase_orders=base.initial_purchase_orders,
        hazards=[h] if h is not None else [],
    )


def run_paired_matrix(
    *,
    seeds: list[int],
    doctrines: list[str],
    fixtures_dir: Path,
    work_dir: Path,
    on_run_complete=None,
) -> tuple[list[PairedHazardPoint], dict[str, dict[str, float]]]:
    """Matrice 5×5 — Pour chaque paire (i, j), mesure :
      - coût moyen avec 1 aléa de i + 1 aléa de j
      - amplification = coût(i+j) / moyenne(coût(i seul), coût(j seul))
      - recovery days par paire

    Pour réduire le nombre de runs, on calcule d'abord les baselines
    (5 single-domain) puis les 15 paires uniques (incl. diagonale).
    """
    work_dir.mkdir(parents=True, exist_ok=True)

    # Phase 1 — baselines : 5 single-domain
    baseline_cost: dict[str, dict[str, float]] = {d: {} for d in doctrines}
    baseline_recovery: dict[str, dict[str, float]] = {d: {} for d in doctrines}
    for dom in DOMAINS:
        for d in doctrines:
            costs = []
            recs = []
            for seed in seeds:
                scen = _build_single_domain_scenario(seed, dom, fixtures_dir)
                db_path = work_dir / f"single_{dom}_{seed}_{d}.db"
                result = run_doctrine(scen, d, db_path, fixtures_dir=fixtures_dir)
                costs.append(compute_kpis(scen, result).total_cost_eur)
                recs.append(compute_time_to_recover(result, shock_day=3))
                if on_run_complete is not None:
                    on_run_complete("single", dom, seed, d)
            baseline_cost[d][dom] = statistics.mean(costs) if costs else 0
            baseline_recovery[d][dom] = statistics.mean(recs) if recs else 0

    # Phase 2 — paires (15 uniques avec diagonale)
    points: list[PairedHazardPoint] = []
    recovery_per_pair: dict[str, dict[str, float]] = {d: {} for d in doctrines}
    for i, dom_a in enumerate(DOMAINS):
        for dom_b in DOMAINS[i:]:
            for d in doctrines:
                costs = []
                recs = []
                for seed in seeds:
                    scen = _build_paired_scenario(
                        seed, dom_a, dom_b, fixtures_dir,
                    )
                    db_path = work_dir / f"pair_{dom_a}_{dom_b}_{seed}_{d}.db"
                    result = run_doctrine(
                        scen, d, db_path, fixtures_dir=fixtures_dir,
                    )
                    costs.append(compute_kpis(scen, result).total_cost_eur)
                    recs.append(compute_time_to_recover(result, shock_day=3))
                    if on_run_complete is not None:
                        on_run_complete("pair", f"{dom_a}_{dom_b}", seed, d)
                cost_mean = statistics.mean(costs) if costs else 0
                baseline_avg = (
                    baseline_cost[d][dom_a] + baseline_cost[d][dom_b]
                ) / 2
                ampli = cost_mean / baseline_avg if baseline_avg > 0 else 0
                points.append(PairedHazardPoint(
                    doctrine=d,
                    domain_a=dom_a,
                    domain_b=dom_b,
                    n_runs=len(costs),
                    cost_mean=cost_mean,
                    cost_baseline_mean=baseline_avg,
                    amplification=ampli,
                ))
                key = f"{dom_a}_{dom_b}"
                recovery_per_pair[d][key] = (
                    statistics.mean(recs) if recs else 0
                )

    return points, recovery_per_pair
