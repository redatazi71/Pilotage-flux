"""Étude §24.10.3 — Résilience par zone décisionnelle.

Injecte 1 hazard d'un domaine donné à profondeur temporelle variable
(gelée j3 / négociable j35 / libre j95) sur horizon 120j, puis
mesure l'impact QCDS par doctrine.

Grille : 5 domaines × 3 zones × 4 doctrines × 5 seeds = 300 runs.

Objectif doctrinal : identifier laquelle des 3 zones décisionnelles
bénéficie le plus de chaque doctrine.

Sortie :
  - docs/charts/zone_study_ampli.png (heatmap 3 zones × 5 domaines
    par doctrine)
  - docs/charts/zone_study_recovery.png
  - docs/cadrage_v4_zone_study_data.md (tables amp + recovery)
"""
from __future__ import annotations

import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pilotage_flux.comparative.kpis import compute_kpis
from pilotage_flux.comparative.random_scenario import (
    RandomScenarioSpec,
    generate_random_scenario,
)
from pilotage_flux.comparative.resilience import (
    DOMAINS,
    DOMAIN_TO_HAZARD,
    _build_hazard,
    compute_time_to_recover,
)
from pilotage_flux.comparative.runner import run_doctrine
from pilotage_flux.comparative.scenario import DOCTRINES, Scenario
from pilotage_flux.data_factory import DEFAULT_SPEC, generate_random_fixtures


HERE = Path(__file__).resolve().parent
CHARTS_DIR = HERE / "charts"
DATA_MD = HERE / "cadrage_v4_zone_study_data.md"

DOCTRINE_LABELS = {
    "of": "OF", "flux": "FLUX",
    "of_event": "OF+EVENT", "event": "EVENT",
}
DOMAIN_LABELS = {
    "appro": "Appro", "logi": "Logi", "qual": "Qual",
    "prod": "Prod", "dem": "Dem",
}
ZONE_LABELS = {
    "gelee": "Gelée (j3)", "negociable": "Négociable (j35)",
    "libre": "Libre (j95)",
}

# Zone → jour d'injection (au centre de la zone canonique 14/70/270)
ZONE_TO_DAY = {"gelee": 3, "negociable": 35, "libre": 95}
ZONES = list(ZONE_TO_DAY)

HORIZON_DAYS = 120
SEEDS = list(range(600, 605))


@dataclass
class ZonePoint:
    doctrine: str
    domain: str
    zone: str
    n_runs: int
    cost_mean: float
    cost_baseline: float  # coût sans hazard
    amplification: float
    recovery_mean: float


def _build_zone_scenario(
    base_seed: int,
    domain: str,
    zone: str,
    fixtures_dir: Path,
) -> Scenario:
    """Génère un scénario avec 1 seul hazard du domaine à la zone donnée."""
    import random as _r
    rng = _r.Random(base_seed)
    spec = RandomScenarioSpec(
        n_hazards=0,
        n_sales_orders=20,
        horizon_days=HORIZON_DAYS,
    )
    base = generate_random_scenario(spec, seed=base_seed,
                                     fixtures_dir=fixtures_dir)
    day = ZONE_TO_DAY[zone]
    h = _build_hazard(rng, DOMAIN_TO_HAZARD[domain], day=day,
                      fixtures_dir=fixtures_dir)
    return Scenario(
        name=f"zone_{domain}_{zone}_seed{base_seed}",
        seed=base_seed,
        horizon_days=HORIZON_DAYS,
        horizon_start=base.horizon_start,
        initial_sales_orders=base.initial_sales_orders,
        initial_stocks=base.initial_stocks,
        initial_purchase_orders=base.initial_purchase_orders,
        hazards=[h] if h is not None else [],
    )


def _build_baseline_scenario(base_seed: int, fixtures_dir: Path) -> Scenario:
    """Scénario sans hazards — baseline pour amplification."""
    spec = RandomScenarioSpec(
        n_hazards=0,
        n_sales_orders=20,
        horizon_days=HORIZON_DAYS,
    )
    base = generate_random_scenario(spec, seed=base_seed,
                                     fixtures_dir=fixtures_dir)
    return Scenario(
        name=f"baseline_seed{base_seed}",
        seed=base_seed,
        horizon_days=HORIZON_DAYS,
        horizon_start=base.horizon_start,
        initial_sales_orders=base.initial_sales_orders,
        initial_stocks=base.initial_stocks,
        initial_purchase_orders=base.initial_purchase_orders,
        hazards=[],
    )


def run_zone_matrix(fixtures_dir: Path, work_dir: Path) -> list[ZonePoint]:
    work_dir.mkdir(parents=True, exist_ok=True)
    points: list[ZonePoint] = []
    doctrines = list(DOCTRINES)

    # Phase 1 : baselines (sans hazard) pour normalisation
    baseline_cost: dict[str, list[float]] = {d: [] for d in doctrines}
    print(f"\n=== Phase 1 : baselines (sans hazard) — "
          f"{len(SEEDS)*len(doctrines)} runs ===")
    for seed in SEEDS:
        scen = _build_baseline_scenario(seed, fixtures_dir)
        for d in doctrines:
            db_path = work_dir / f"baseline_{seed}_{d}.db"
            result = run_doctrine(scen, d, db_path,
                                   fixtures_dir=fixtures_dir)
            k = compute_kpis(scen, result)
            baseline_cost[d].append(k.total_cost_eur)
    baseline_mean = {d: statistics.mean(baseline_cost[d]) for d in doctrines}
    for d in doctrines:
        print(f"  {d:<10} baseline coût moyen = "
              f"{baseline_mean[d]:>10.0f} €")

    # Phase 2 : hazards par zone × domaine × doctrine
    total = len(DOMAINS) * len(ZONES) * len(doctrines) * len(SEEDS)
    print(f"\n=== Phase 2 : matrice zone×domaine×doctrine — "
          f"{total} runs ===")
    done = 0
    for domain in DOMAINS:
        for zone in ZONES:
            for d in doctrines:
                costs = []
                recs = []
                for seed in SEEDS:
                    scen = _build_zone_scenario(
                        seed, domain, zone, fixtures_dir,
                    )
                    db_path = work_dir / (
                        f"zone_{domain}_{zone}_{seed}_{d}.db"
                    )
                    result = run_doctrine(
                        scen, d, db_path, fixtures_dir=fixtures_dir,
                    )
                    k = compute_kpis(scen, result)
                    costs.append(k.total_cost_eur)
                    recs.append(
                        compute_time_to_recover(
                            result, shock_day=ZONE_TO_DAY[zone],
                        )
                    )
                    done += 1
                cost_mean = statistics.mean(costs) if costs else 0
                base = baseline_mean[d]
                amp = cost_mean / base if base > 0 else 0
                rec = statistics.mean(recs) if recs else 0
                points.append(ZonePoint(
                    doctrine=d, domain=domain, zone=zone,
                    n_runs=len(costs),
                    cost_mean=cost_mean, cost_baseline=base,
                    amplification=amp, recovery_mean=rec,
                ))
            print(f"  ... {done}/{total}")
    return points


def _matrix(points: list[ZonePoint], doctrine: str, attr: str
            ) -> list[list[float]]:
    """Zones (row) × Domains (col) → attr value."""
    m = []
    for zone in ZONES:
        row = []
        for domain in DOMAINS:
            p = next((p for p in points if p.doctrine == doctrine
                       and p.zone == zone and p.domain == domain), None)
            row.append(getattr(p, attr) if p else 0.0)
        m.append(row)
    return m


def chart_zone_heatmaps(points: list[ZonePoint], attr: str,
                          title: str, filename: str, fmt: str = ".2f",
                          cmap: str = "RdYlGn_r") -> None:
    fig, axes = plt.subplots(1, 4, figsize=(20, 5))
    doctrines = ["of", "flux", "of_event", "event"]
    all_vals = []
    for d in doctrines:
        all_vals.extend([v for row in _matrix(points, d, attr) for v in row])
    vmin = min(all_vals) if all_vals else 0
    vmax = max(all_vals) if all_vals else 1

    for i, d in enumerate(doctrines):
        m = _matrix(points, d, attr)
        arr = np.array(m)
        ax = axes[i]
        im = ax.imshow(arr, cmap=cmap, vmin=vmin, vmax=vmax)
        ax.set_xticks(range(len(DOMAINS)))
        ax.set_xticklabels([DOMAIN_LABELS[d] for d in DOMAINS])
        ax.set_yticks(range(len(ZONES)))
        ax.set_yticklabels([ZONE_LABELS[z] for z in ZONES])
        ax.set_title(DOCTRINE_LABELS[d])
        for zi in range(len(ZONES)):
            for di in range(len(DOMAINS)):
                ax.text(di, zi, f"{arr[zi, di]:{fmt}}",
                        ha="center", va="center", fontsize=9,
                        color="white" if arr[zi, di] > (vmin + vmax)/2
                        else "black")
        fig.colorbar(im, ax=ax, fraction=0.05)
    fig.suptitle(title, fontsize=14)
    plt.tight_layout()
    plt.savefig(CHARTS_DIR / filename, dpi=100, bbox_inches="tight")
    plt.close(fig)
    print(f"✓ {CHARTS_DIR / filename}")


def write_data_md(points: list[ZonePoint]) -> None:
    lines = ["# §24.10.3 — Étude par zone décisionnelle (données brutes)",
             "",
             f"Horizon {HORIZON_DAYS}j, {len(SEEDS)} seeds par cellule, "
             f"total {len(points) * len(SEEDS) + len(DOCTRINES) * len(SEEDS)} runs.",
             "",
             "## Amplification de coût (coût avec hazard / baseline sans hazard)",
             ""]
    for d in ["of", "flux", "of_event", "event"]:
        lines.append(f"### Doctrine {DOCTRINE_LABELS[d]}")
        lines.append("")
        header = "| Zone | " + " | ".join(
            DOMAIN_LABELS[dom] for dom in DOMAINS) + " |"
        sep = "|---" * (len(DOMAINS) + 1) + "|"
        lines.append(header)
        lines.append(sep)
        for zone in ZONES:
            row = [ZONE_LABELS[zone]]
            for domain in DOMAINS:
                p = next((p for p in points if p.doctrine == d
                          and p.zone == zone and p.domain == domain), None)
                row.append(f"{p.amplification:.2f}" if p else "—")
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    lines.append("## Time-to-recover (jours)")
    lines.append("")
    for d in ["of", "flux", "of_event", "event"]:
        lines.append(f"### Doctrine {DOCTRINE_LABELS[d]}")
        lines.append("")
        header = "| Zone | " + " | ".join(
            DOMAIN_LABELS[dom] for dom in DOMAINS) + " |"
        sep = "|---" * (len(DOMAINS) + 1) + "|"
        lines.append(header)
        lines.append(sep)
        for zone in ZONES:
            row = [ZONE_LABELS[zone]]
            for domain in DOMAINS:
                p = next((p for p in points if p.doctrine == d
                          and p.zone == zone and p.domain == domain), None)
                row.append(f"{p.recovery_mean:.1f}" if p else "—")
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    DATA_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓ {DATA_MD}")


def main() -> int:
    n_baseline = len(SEEDS) * len(DOCTRINES)
    n_zone = len(DOMAINS) * len(ZONES) * len(DOCTRINES) * len(SEEDS)
    print(f"=== Étude par zone décisionnelle §24.10.3 ===")
    print(f"Horizon {HORIZON_DAYS}j, zones (j3, j35, j95)")
    print(f"Baselines : {n_baseline} runs")
    print(f"Matrice   : {n_zone} runs")
    print(f"Total     : {n_baseline + n_zone} runs")

    with TemporaryDirectory(prefix="zone_study_") as tmp:
        work = Path(tmp)
        fix_dir = work / "fix"
        generate_random_fixtures(DEFAULT_SPEC, seed=42, out_dir=fix_dir)
        points = run_zone_matrix(fix_dir, work / "runs")

    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    print("\n→ Génération des graphiques")
    chart_zone_heatmaps(points, "amplification",
                          "Amplification de coût — zones × domaines",
                          "zone_study_ampli.png", fmt=".2f")
    chart_zone_heatmaps(points, "recovery_mean",
                          "Time-to-recover (jours) — zones × domaines",
                          "zone_study_recovery.png", fmt=".1f")
    write_data_md(points)
    print("\nÉtude par zone terminée.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
