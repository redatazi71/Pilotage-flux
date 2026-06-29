"""Apprentissage en boucle longue du filtre dual (L8.3).

V3 capture des recettes mémoire à chaque clôture P4 (cf. dual_memory.py).
L8.3 boucle ces captures sur N runs successifs : entre deux runs, on
analyse les recettes retenues et on ajuste légèrement les seuils du
filtre dual pour favoriser des actions proportionnées au profil observé.

Heuristique d'ajustement (data-driven, transparente) :

  Rappel de la grammaire des seuils — `_level_from_score` retourne :
    score < watch_th         → inform
    score < correct_local_th → watch
    score < replan_local_th  → correct_local
    score < escalate_th      → replan_local
    score < replan_global_th → escalate
    else                     → replan_global

  Donc pour déplacer une déviation depuis `escalate` vers `replan_local`,
  il faut **monter** `escalate_th` au-dessus du score observé. Le seuil
  est ce que la déviation doit franchir POUR entrer dans le niveau —
  monter = rendre le niveau supérieur plus dur à atteindre.

  - Si beaucoup d'actions `replan_global` : monte replan_global_th pour
    refluer vers escalate.
  - Si beaucoup d'actions `escalate` : monte escalate_th pour refluer
    vers replan_local.

  - Step = `learning_rate` (default 5%) multiplicateur ×(1+lr). Bornes :
    seuils restent ≤ DEFAULT_MAX_THRESHOLD.

KPI cible : ratio (actions_locales / total_actions) augmente au fil des runs.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from pilotage_flux.comparative.kpis import KpiSet, compute_kpis
from pilotage_flux.comparative.runner import DEFAULT_FIXTURES_DIR, run_event_doctrine
from pilotage_flux.comparative.scenario import Scenario, jitter_scenario
from pilotage_flux.db import db_session
from pilotage_flux.parameters import get_num


DEFAULT_LEARNING_RATE = 0.10
DEFAULT_MIN_THRESHOLD = 0.20
DEFAULT_MAX_THRESHOLD = 10.0
DEFAULT_N_ITERATIONS = 10

TUNABLE_THRESHOLDS = (
    "tolerance_threshold_watch",
    "tolerance_threshold_correct_local",
    "tolerance_threshold_replan_local",
    "tolerance_threshold_escalate",
    "tolerance_threshold_replan_global",
)


@dataclass
class LearningIteration:
    iter_idx: int
    kpis: KpiSet
    thresholds_before: dict[str, float]
    thresholds_after: dict[str, float]
    n_retained_recipes: int
    actions_local_count: int
    actions_global_count: int

    @property
    def local_ratio(self) -> float:
        total = self.actions_local_count + self.actions_global_count
        if total == 0:
            return 0.0
        return self.actions_local_count / total


@dataclass
class LearningRun:
    scenario_name: str
    iterations: list[LearningIteration] = field(default_factory=list)

    @property
    def initial_local_ratio(self) -> float:
        return self.iterations[0].local_ratio if self.iterations else 0.0

    @property
    def final_local_ratio(self) -> float:
        return self.iterations[-1].local_ratio if self.iterations else 0.0

    @property
    def converged(self) -> bool:
        """Considère l'apprentissage convergé si le ratio local est resté
        stable (±0.02) sur les 3 dernières itérations."""
        if len(self.iterations) < 3:
            return False
        last3 = [it.local_ratio for it in self.iterations[-3:]]
        return max(last3) - min(last3) <= 0.02


def auto_tune_thresholds(
    conn: sqlite3.Connection,
    *,
    learning_rate: float = DEFAULT_LEARNING_RATE,
    min_threshold: float = DEFAULT_MIN_THRESHOLD,
    max_threshold: float = DEFAULT_MAX_THRESHOLD,
) -> dict[str, tuple[float, float]]:
    """Analyse les actions filtre dual du run courant et ajuste les seuils
    tolerance_threshold_* pour réduire l'escalade.

    Logique : pour déplacer une déviation d'escalate vers replan_local,
    on **monte** tolerance_threshold_escalate au-dessus du score max
    observé en escalate. Approximation : on monte par incrément×(1+lr).

    Retourne {threshold_name: (old, new)}.
    """
    counts = conn.execute(
        """
        SELECT action_level, COUNT(*) AS n
        FROM tolerance_filter_decisions
        WHERE triggered_at IS NOT NULL
        GROUP BY action_level
        """
    ).fetchall()
    by_level = {r["action_level"]: int(r["n"]) for r in counts}
    total = sum(by_level.values())
    if total == 0:
        return {}

    changes: dict[str, tuple[float, float]] = {}

    # Heuristique : on monte les seuils qui sont trop "bas" (trop d'actions
    # passent au-dessus). Plus la part d'un niveau supérieur est élevée,
    # plus on monte son seuil pour refluer vers le niveau inférieur.
    targets = [
        ("replan_global", "tolerance_threshold_replan_global", 0.05),
        ("escalate", "tolerance_threshold_escalate", 0.10),
    ]
    for level, param_name, ratio_trigger in targets:
        if by_level.get(level, 0) / total <= ratio_trigger:
            continue
        current = get_num(
            conn, scope="global", scope_ref=None, name=param_name, default=None,
        )
        if current is None:
            continue
        new_val = min(float(max_threshold), float(current) * (1 + learning_rate))
        if abs(new_val - float(current)) < 1e-6:
            continue
        # Versionning : ferme l'ancien param + insère nouvelle version
        old_version_row = conn.execute(
            "SELECT MAX(version) AS v FROM parameters "
            "WHERE scope = 'global' AND scope_ref IS NULL AND name = ?",
            (param_name,),
        ).fetchone()
        old_version = int(old_version_row["v"]) if old_version_row and old_version_row["v"] else 1
        conn.execute(
            "UPDATE parameters SET valid_to = datetime('now') "
            "WHERE scope = 'global' AND scope_ref IS NULL "
            "AND name = ? AND valid_to IS NULL",
            (param_name,),
        )
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num, version) "
            "VALUES ('global', NULL, ?, ?, ?)",
            (param_name, new_val, old_version + 1),
        )
        changes[param_name] = (float(current), new_val)

    return changes


def _snapshot_thresholds(conn: sqlite3.Connection) -> dict[str, float]:
    out: dict[str, float] = {}
    for name in TUNABLE_THRESHOLDS:
        v = get_num(
            conn, scope="global", scope_ref=None, name=name, default=None,
        )
        if v is not None:
            out[name] = float(v)
    return out


def run_learning_loop(
    scenario: Scenario,
    work_dir: Path,
    *,
    n_iterations: int = DEFAULT_N_ITERATIONS,
    learning_rate: float = DEFAULT_LEARNING_RATE,
    fixtures_dir: Path = DEFAULT_FIXTURES_DIR,
    jitter: bool = False,
) -> LearningRun:
    """Lance N itérations EVENT successives en propageant les seuils appris.

    Si `jitter=True`, chaque itération utilise une seed différente (variance
    réelle) pour éviter que les seuils sur-apprennent un seul scénario.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    run = LearningRun(scenario_name=scenario.name)
    learned: dict[str, float] = {}

    for i in range(n_iterations):
        db_path = work_dir / f"iter_{i:03d}.db"
        scen = (
            jitter_scenario(scenario, seed=scenario.seed + i)
            if jitter else scenario
        )
        result = run_event_doctrine(
            scen, db_path, fixtures_dir=fixtures_dir,
            parameter_overrides=learned,
        )
        kpi = compute_kpis(scen, result)
        with db_session(db_path) as conn:
            before = _snapshot_thresholds(conn)
            changes = auto_tune_thresholds(
                conn, learning_rate=learning_rate,
            )
            after = _snapshot_thresholds(conn)

            local = conn.execute(
                "SELECT COUNT(*) AS n FROM tolerance_filter_decisions "
                "WHERE action_level IN ('correct_local', 'replan_local') "
                "AND triggered_at IS NOT NULL"
            ).fetchone()
            globl = conn.execute(
                "SELECT COUNT(*) AS n FROM tolerance_filter_decisions "
                "WHERE action_level IN ('escalate', 'replan_global') "
                "AND triggered_at IS NOT NULL"
            ).fetchone()
            retained = conn.execute(
                "SELECT COUNT(*) AS n FROM memory_recipes WHERE is_retained = 1"
            ).fetchone()

        # Propage les seuils appris pour la prochaine itération
        for name, (_, new_val) in changes.items():
            learned[name] = new_val

        run.iterations.append(LearningIteration(
            iter_idx=i,
            kpis=kpi,
            thresholds_before=before,
            thresholds_after=after,
            n_retained_recipes=int(retained["n"]) if retained else 0,
            actions_local_count=int(local["n"]) if local else 0,
            actions_global_count=int(globl["n"]) if globl else 0,
        ))
    return run


def build_learning_report(run: LearningRun) -> str:
    """Rapport Markdown de la boucle d'apprentissage."""
    lines: list[str] = []
    lines.append(f"# Apprentissage boucle longue — scénario `{run.scenario_name}`")
    lines.append("")
    lines.append(f"{len(run.iterations)} itérations EVENT successives, "
                 "seuils tolerance_threshold_* auto-ajustés entre runs.")
    lines.append("")
    lines.append(
        f"**Ratio actions locales initial → final** : "
        f"{run.initial_local_ratio:.1%} → {run.final_local_ratio:.1%} "
        f"({'convergé' if run.converged else 'non convergé'})"
    )
    lines.append("")
    lines.append("| Iter | Local | Global | Ratio local | Retained | Δ seuils |")
    lines.append("|---|---|---|---|---|---|")
    for it in run.iterations:
        diff_keys = [
            k for k in it.thresholds_after
            if abs(it.thresholds_after.get(k, 0) - it.thresholds_before.get(k, 0)) > 1e-6
        ]
        diffs = ", ".join(
            f"{k.replace('tolerance_threshold_', '')}={it.thresholds_after[k]:.3f}"
            for k in diff_keys
        ) or "—"
        lines.append(
            f"| {it.iter_idx} | {it.actions_local_count} | {it.actions_global_count} | "
            f"{it.local_ratio:.1%} | {it.n_retained_recipes} | {diffs} |"
        )
    lines.append("")
    return "\n".join(lines)
