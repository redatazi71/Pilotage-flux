"""Option A QCDS — étude QCDS sur 4 dimensions × 4 doctrines.

Re-run 20 seeds × 4 doctrines = 80 runs avec calcul des 4 KPIs
QCDS (Quality, Cost, Delivery, Stability) :

  Q — quantity_compliance (Option A nouveau)
  C — total_cost_eur
  D — disponibility_so_level (Point 2, tol=3j)
  S — nervousness

Produit :
  - docs/charts/qcds_4_dimensions.png  (radar 4 doctrines × 4 axes)
  - docs/cadrage_v4_qcds_data.md
"""

from __future__ import annotations

import statistics
from pathlib import Path
from tempfile import TemporaryDirectory

import matplotlib.pyplot as plt
import numpy as np

from pilotage_flux.comparative.kpis import compute_kpis
from pilotage_flux.comparative.random_scenario import (
    RandomScenarioSpec,
    generate_random_scenario,
)
from pilotage_flux.comparative.runner import run_doctrine
from pilotage_flux.comparative.scenario import DOCTRINES
from pilotage_flux.data_factory import DEFAULT_SPEC, generate_random_fixtures


HERE = Path(__file__).resolve().parent
CHARTS_DIR = HERE / "charts"
DATA_MD = HERE / "cadrage_v4_qcds_data.md"

DOCTRINE_LABELS = {
    "of": "OF", "flux": "FLUX",
    "of_event": "OF+EVENT", "event": "EVENT",
}
COLORS = {
    "of": "#888888", "flux": "#1f77b4",
    "of_event": "#ff7f0e", "event": "#2ca02c",
}

SEEDS = list(range(1000, 1020))  # 20 seeds


def run_study() -> dict[str, dict[str, list[float]]]:
    """results[doctrine] = {q, c, d, s, raw_cost} de mesures."""
    print(f"=== Étude QCDS — {len(SEEDS) * len(DOCTRINES)} runs ===")
    results = {d: {"q": [], "c": [], "d": [], "s": [], "raw_cost": []}
               for d in DOCTRINES}

    with TemporaryDirectory(prefix="qcds_") as tmp:
        work = Path(tmp)
        fix_dir = work / "fix"
        generate_random_fixtures(DEFAULT_SPEC, seed=42, out_dir=fix_dir)

        for seed in SEEDS:
            scen = generate_random_scenario(
                RandomScenarioSpec(), seed=seed,
                fixtures_dir=fix_dir,
            )
            for d in DOCTRINES:
                db_path = work / f"qcds_{seed}_{d}.db"
                # Tolérance modérée (3j) — recommandée par §24.8.6
                result = run_doctrine(
                    scen, d, db_path, fixtures_dir=fix_dir,
                    evaluate_rejections=True,
                    late_threshold_days=3,
                )
                kpi = compute_kpis(scen, result)
                results[d]["q"].append(kpi.quantity_compliance)
                results[d]["c"].append(kpi.total_cost_eur)
                results[d]["d"].append(kpi.disponibility_so_level)
                results[d]["s"].append(kpi.nervousness)
                results[d]["raw_cost"].append(kpi.total_cost_eur)
    return results


def chart_radar(results: dict) -> None:
    """Radar 4 axes (Q, C, D, S) × 4 doctrines."""
    fig, ax = plt.subplots(figsize=(10, 10),
                            subplot_kw=dict(projection="polar"))

    # Normalisation : 1.0 = meilleur observé, 0.0 = pire
    # Q : higher is better (1.0 = livre toute la quantité)
    # C : lower is better → inverser
    # D : higher is better (1.0 = toutes SOs livrées)
    # S : lower is better → inverser
    q_min = min(statistics.mean(results[d]["q"]) for d in DOCTRINES)
    q_max = max(statistics.mean(results[d]["q"]) for d in DOCTRINES)
    c_min = min(statistics.mean(results[d]["c"]) for d in DOCTRINES)
    c_max = max(statistics.mean(results[d]["c"]) for d in DOCTRINES)
    d_min = min(statistics.mean(results[d]["d"]) for d in DOCTRINES)
    d_max = max(statistics.mean(results[d]["d"]) for d in DOCTRINES)
    s_min = min(statistics.mean(results[d]["s"]) for d in DOCTRINES)
    s_max = max(statistics.mean(results[d]["s"]) for d in DOCTRINES)

    def norm(v, mn, mx, invert=False):
        if mx == mn:
            return 0.5
        r = (v - mn) / (mx - mn)
        return 1.0 - r if invert else r

    axes_labels = [
        "Q\nQuantity\ncompliance",
        "C\nCoût\n(inversé)",
        "D\nDélai\n(disponibilité)",
        "S\nStabilité\n(nervosité inv.)",
    ]
    n = len(axes_labels)
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
    angles += angles[:1]

    for doc in DOCTRINES:
        q = norm(statistics.mean(results[doc]["q"]), q_min, q_max)
        c = norm(statistics.mean(results[doc]["c"]), c_min, c_max,
                 invert=True)
        dl = norm(statistics.mean(results[doc]["d"]), d_min, d_max)
        s = norm(statistics.mean(results[doc]["s"]), s_min, s_max,
                 invert=True)
        vals = [q, c, dl, s, q]
        ax.plot(angles, vals, marker="o", linewidth=2,
                 color=COLORS[doc], label=DOCTRINE_LABELS[doc])
        ax.fill(angles, vals, alpha=0.12, color=COLORS[doc])

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(axes_labels, fontsize=11)
    ax.set_ylim(0, 1.05)
    ax.set_yticks([0.25, 0.5, 0.75, 1.0])
    ax.set_yticklabels(["25 %", "50 %", "75 %", "100 %"], fontsize=8)
    ax.grid(True, alpha=0.4)
    ax.set_title(
        "QCDS — 4 objectifs industriels × 4 doctrines (80 runs)\n"
        "(1.0 = meilleur observé sur ce protocole, 0.0 = pire)",
        fontsize=12, fontweight="bold", pad=20,
    )
    ax.legend(loc="upper right", bbox_to_anchor=(1.30, 1.10),
               fontsize=10, frameon=True)

    plt.tight_layout()
    out = CHARTS_DIR / "qcds_4_dimensions.png"
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"✓ {out}")


def write_data(results: dict) -> None:
    lines = ["# QCDS — 80 runs sur 4 doctrines × 4 dimensions", ""]
    lines.append(
        "| Doctrine | Q (compliance) | C (€) | D (dispo) | S (nervosité) |"
    )
    lines.append("|---|---|---|---|---|")
    for d in DOCTRINES:
        q = statistics.mean(results[d]["q"])
        c = statistics.mean(results[d]["c"])
        dl = statistics.mean(results[d]["d"])
        s = statistics.mean(results[d]["s"])
        lines.append(
            f"| {DOCTRINE_LABELS[d]} | {q:.4f} | "
            f"{c:,.0f} €".replace(",", " ") + f" | "
            f"{dl:.4f} | {s:.3f} |"
        )
    lines.append("")
    lines.append("## Δ vs OF par dimension")
    lines.append("")
    of_q = statistics.mean(results["of"]["q"])
    of_c = statistics.mean(results["of"]["c"])
    of_d = statistics.mean(results["of"]["d"])
    of_s = statistics.mean(results["of"]["s"])
    lines.append("| Doctrine | Δ Q | Δ C | Δ D | Δ S |")
    lines.append("|---|---|---|---|---|")
    for d in DOCTRINES:
        if d == "of":
            continue
        q = statistics.mean(results[d]["q"])
        c = statistics.mean(results[d]["c"])
        dl = statistics.mean(results[d]["d"])
        s = statistics.mean(results[d]["s"])
        lines.append(
            f"| {DOCTRINE_LABELS[d]} | "
            f"{(q - of_q) * 100:+.1f} pp | "
            f"{(c - of_c) / of_c * 100:+.1f} % | "
            f"{(dl - of_d) * 100:+.1f} pp | "
            f"{(s - of_s) / of_s * 100:+.1f} % |"
        )
    DATA_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓ {DATA_MD}")


def main() -> None:
    results = run_study()
    chart_radar(results)
    write_data(results)
    print("\nÉtude QCDS terminée.")


if __name__ == "__main__":
    main()
