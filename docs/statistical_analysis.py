"""Ext-j — Analyse statistique post-hoc des runs master v2.

Applique aux résultats du protocole étendu :
  - Bootstrap IC 95 % sur chaque gain (2000 resamples des seeds).
  - Wilcoxon signed-rank paired par cellule (même seed → même choc).
  - Cliff's delta effect size (convention : |δ| > 0.474 = grand effet).

Entrée : docs/master_v2_runs.csv (ou le CSV fourni via --csv).
Sortie : docs/statistical_analysis_results.md avec les tables prêtes à
insérer dans le §5 du paper.

Ce script ne relance aucun run — il ré-analyse les CSV existants.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, median, stdev
from typing import Dict, List, Tuple

try:
    from scipy import stats as _sps  # noqa
    HAVE_SCIPY = True
except ImportError:
    HAVE_SCIPY = False


METRICS_HIGHER_BETTER = {"otif", "q_compliance", "d_dispo",
                         "of_closed_ratio", "recovery_success_rate"}
METRICS_LOWER_BETTER = {"cost_per_u", "wip_avg", "wip_sd", "nervousness",
                        "rupture_pct", "recovery_days",
                        "recovery_days_conditional"}
KEY_METRICS = ["otif", "cost_per_u", "wip_sd", "nervousness",
               "recovery_success_rate", "recovery_days_conditional"]


def load_runs(csv_path: Path) -> List[dict]:
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = [r for r in reader if r.get("status") == "ok"]
    for r in rows:
        for k, v in list(r.items()):
            if v in ("", None):
                continue
            try:
                if "." in v:
                    r[k] = float(v)
                else:
                    r[k] = int(v)
            except (ValueError, TypeError):
                pass
    return rows


def group_by_cell(rows: List[dict]) -> Dict[Tuple, Dict[str, List[dict]]]:
    """Groupe par (level, shock_type, seed) → {doctrine: run_row}.

    Une cellule complète contient tous les runs avec mêmes paramètres et
    même seed mais doctrines différentes. C'est la base du paired test.
    """
    cells: Dict[Tuple, Dict[str, dict]] = defaultdict(dict)
    for r in rows:
        key = (r["level"], r["shock_type"], r["seed"])
        cells[key][r["config_tag"]] = r
    return cells


def paired_deltas(
    cells: Dict[Tuple, Dict[str, dict]],
    doctrine_a: str,
    doctrine_b: str,
    metric: str,
) -> List[Tuple[float, float]]:
    """Retourne les paires (valeur_A, valeur_B) sur les cellules où les
    deux doctrines ont un run avec la métrique renseignée."""
    pairs: List[Tuple[float, float]] = []
    for key, byd in cells.items():
        if doctrine_a not in byd or doctrine_b not in byd:
            continue
        va = byd[doctrine_a].get(metric)
        vb = byd[doctrine_b].get(metric)
        if va is None or vb is None:
            continue
        if not isinstance(va, (int, float)) or not isinstance(vb, (int, float)):
            continue
        pairs.append((float(va), float(vb)))
    return pairs


def bootstrap_ci(
    values: List[float],
    n_iter: int = 2000,
    ci: float = 0.95,
    seed: int = 42,
) -> Tuple[float, float, float]:
    """Bootstrap non-paramétrique de la moyenne. Retourne (mean, lo, hi)."""
    if not values:
        return 0.0, 0.0, 0.0
    rng = random.Random(seed)
    n = len(values)
    means: List[float] = []
    for _ in range(n_iter):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo_idx = int((1 - ci) / 2 * n_iter)
    hi_idx = int((1 + ci) / 2 * n_iter) - 1
    return mean(values), means[lo_idx], means[hi_idx]


def wilcoxon_paired(a: List[float], b: List[float]) -> Tuple[float, float] | None:
    """Wilcoxon signed-rank test paired. Retourne (statistic, p_value).
    None si scipy indisponible ou moins de 6 paires non-nulles."""
    if not HAVE_SCIPY or len(a) < 6:
        return None
    diffs = [x - y for x, y in zip(a, b) if x != y]
    if len(diffs) < 6:
        return None
    try:
        res = _sps.wilcoxon(a, b, zero_method="wilcox", alternative="two-sided")
        return float(res.statistic), float(res.pvalue)
    except Exception:
        return None


def cliffs_delta(a: List[float], b: List[float]) -> float:
    """Cliff's delta : proportion de paires (ai, bj) où ai > bj moins
    celles où ai < bj. Range [-1, 1]. Convention :
      |δ| < 0.147 négligeable ; < 0.33 petit ; < 0.474 moyen ; sinon grand.
    """
    if not a or not b:
        return 0.0
    n_gt = n_lt = 0
    for x in a:
        for y in b:
            if x > y:
                n_gt += 1
            elif x < y:
                n_lt += 1
    total = len(a) * len(b)
    return (n_gt - n_lt) / total if total > 0 else 0.0


def interpret_cliff(d: float) -> str:
    ad = abs(d)
    if ad < 0.147:
        return "négligeable"
    if ad < 0.33:
        return "petit"
    if ad < 0.474:
        return "moyen"
    return "grand"


def analyze_pair(
    cells: Dict[Tuple, Dict[str, dict]],
    doctrine_a: str,
    doctrine_b: str,
) -> Dict[str, dict]:
    """Comparaison paired entre deux doctrines sur toutes les métriques clé."""
    results: Dict[str, dict] = {}
    for metric in KEY_METRICS:
        pairs = paired_deltas(cells, doctrine_a, doctrine_b, metric)
        if not pairs:
            continue
        values_a = [p[0] for p in pairs]
        values_b = [p[1] for p in pairs]
        deltas = [b_ - a_ for a_, b_ in pairs]  # gain de B relativement à A
        mean_a = mean(values_a)
        mean_b = mean(values_b)
        m_delta, lo_delta, hi_delta = bootstrap_ci(deltas)
        gain_pct = ((mean_b - mean_a) / mean_a * 100.0) if mean_a else 0.0
        wx = wilcoxon_paired(values_a, values_b)
        cd = cliffs_delta(values_a, values_b)
        results[metric] = {
            "n_pairs": len(pairs),
            "mean_a": mean_a,
            "mean_b": mean_b,
            "gain_pct": gain_pct,
            "mean_delta": m_delta,
            "ci_lo": lo_delta,
            "ci_hi": hi_delta,
            "wilcoxon_stat": wx[0] if wx else None,
            "wilcoxon_pvalue": wx[1] if wx else None,
            "cliff_delta": cd,
            "cliff_magnitude": interpret_cliff(cd),
        }
    return results


def fmt_p(p: float | None) -> str:
    if p is None:
        return "n/a"
    if p < 0.001:
        return "< 0.001"
    if p < 0.01:
        return f"{p:.3f}"
    return f"{p:.3f}"


def render_markdown(
    csv_path: Path,
    n_rows: int,
    comparisons: Dict[str, Dict[str, dict]],
) -> str:
    lines: List[str] = []
    lines.append(
        "# Analyse statistique post-hoc — protocole master v2\n\n"
        f"Source : `{csv_path.name}` ({n_rows} runs analysables)\n\n"
        "Cette table applique les tests recommandés en review RFGI :\n"
        "bootstrap IC 95 % sur les gains, Wilcoxon signed-rank paired\n"
        "(même seed → même choc), Cliff's δ pour la taille d'effet.\n\n"
        "Convention Cliff's δ : |δ| < 0.147 négligeable · < 0.33 petit · "
        "< 0.474 moyen · ≥ 0.474 grand.\n"
    )
    for pair_label, results in comparisons.items():
        lines.append(f"\n## {pair_label}\n")
        lines.append(
            "| Métrique | n | Moy. A | Moy. B | Δ (B−A) | IC 95 % Δ "
            "| Gain % | Wilcoxon p | Cliff δ | Effet |\n"
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|:---|"
        )
        for metric, stats in results.items():
            lines.append(
                f"| {metric} | {stats['n_pairs']} "
                f"| {stats['mean_a']:.3f} "
                f"| {stats['mean_b']:.3f} "
                f"| {stats['mean_delta']:+.3f} "
                f"| [{stats['ci_lo']:+.3f}, {stats['ci_hi']:+.3f}] "
                f"| {stats['gain_pct']:+.2f}% "
                f"| {fmt_p(stats['wilcoxon_pvalue'])} "
                f"| {stats['cliff_delta']:+.3f} "
                f"| {stats['cliff_magnitude']} |"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path,
                        default=Path("docs/master_v2_runs.csv"))
    parser.add_argument("--out-md", type=Path,
                        default=Path("docs/statistical_analysis_results.md"))
    parser.add_argument("--out-json", type=Path,
                        default=Path("docs/statistical_analysis_results.json"))
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"[erreur] CSV introuvable : {args.csv}", file=sys.stderr)
        return 1
    rows = load_runs(args.csv)
    if not rows:
        print("[erreur] aucun run 'ok' dans le CSV", file=sys.stderr)
        return 1

    cells = group_by_cell(rows)
    doctrines = sorted({r["config_tag"] for r in rows})
    print(f"[info] doctrines détectées : {doctrines}")
    print(f"[info] {len(rows)} runs ok, {len(cells)} cellules")
    if not HAVE_SCIPY:
        print("[warn] scipy indisponible — Wilcoxon désactivé")

    pairs_to_compare = []
    if "OF" in doctrines and "OF+EVENT" in doctrines:
        pairs_to_compare.append(("OF vs OF+EVENT", "OF", "OF+EVENT"))
    if "OF+EVENT" in doctrines and "FLUX+EVENT" in doctrines:
        pairs_to_compare.append(("OF+EVENT vs FLUX+EVENT", "OF+EVENT",
                                 "FLUX+EVENT"))
    if "OF" in doctrines and "FLUX+EVENT" in doctrines:
        pairs_to_compare.append(("OF vs FLUX+EVENT (cumul)", "OF",
                                 "FLUX+EVENT"))

    comparisons: Dict[str, Dict[str, dict]] = {}
    for label, a, b in pairs_to_compare:
        comparisons[label] = analyze_pair(cells, a, b)

    md = render_markdown(args.csv, len(rows), comparisons)
    args.out_md.write_text(md, encoding="utf-8")
    args.out_json.write_text(
        json.dumps(comparisons, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[ok] {args.out_md}")
    print(f"[ok] {args.out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
