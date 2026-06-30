"""V12.2 — Heuristiques classiques de séquencement (fallback rapide).

Quatre règles de séquencement sont implémentées :

  - **SLACK** (Slack Time First) : tri par (due_date - now - duration)
    croissant → l'OF avec le moins de marge passe en premier.
  - **EDD** (Earliest Due Date) : tri par due_date croissant → l'OF
    avec l'échéance la plus proche en premier.
  - **SPT** (Shortest Processing Time) : tri par durée croissante →
    règle débit, maximise le throughput court terme.
  - **ATC** (Apparent Tardiness Cost) : priorité ∝ 1 / duration ×
    exp(−max(0, slack) / (k × avg_duration)), favorise les jobs
    urgents-courts. K=1.0 par défaut.

Toutes exposent la même API :

    schedule_heuristic(
        of_ids, duration_days, due_day, current_day, freeze_end_day,
        horizon_end_day, kind=HEURISTIC_SLACK,
    ) -> dict[of_id, new_launch_day]
"""

from __future__ import annotations

import math
from typing import Final

HEURISTIC_SLACK: Final[str] = "slack"
HEURISTIC_EDD: Final[str] = "edd"
HEURISTIC_SPT: Final[str] = "spt"
HEURISTIC_ATC: Final[str] = "atc"

HEURISTICS: Final[tuple[str, ...]] = (
    HEURISTIC_SLACK, HEURISTIC_EDD, HEURISTIC_SPT, HEURISTIC_ATC,
)


def schedule_heuristic(
    of_ids: list[str],
    *,
    duration_days: dict[str, int],
    due_day: dict[str, int],
    current_day: dict[str, int],
    freeze_end_day: int,
    horizon_end_day: int,
    kind: str = HEURISTIC_SLACK,
    atc_k: float = 1.0,
) -> dict[str, int]:
    """Calcule un launch_day par OF selon l'heuristique demandée."""
    if kind not in HEURISTICS:
        raise ValueError(
            f"Heuristique inconnue : {kind!r} (attendu : {HEURISTICS})"
        )
    if not of_ids:
        return {}

    n = len(of_ids)
    width = max(1, horizon_end_day - freeze_end_day)
    avg_dur = sum(duration_days.values()) / max(1, len(duration_days))

    if kind == HEURISTIC_SLACK:
        sort_key = lambda oid: due_day[oid] - duration_days[oid]
    elif kind == HEURISTIC_EDD:
        sort_key = lambda oid: due_day[oid]
    elif kind == HEURISTIC_SPT:
        sort_key = lambda oid: duration_days[oid]
    else:  # ATC
        # On trie par priorité ATC décroissante → équivalent à trier
        # par -priority croissant
        def atc_priority(oid: str) -> float:
            dur = duration_days[oid]
            slack = max(0, due_day[oid] - current_day[oid] - dur)
            if dur <= 0:
                return -float("inf")
            return (1.0 / dur) * math.exp(
                -slack / max(1e-9, atc_k * avg_dur)
            )
        sort_key = lambda oid: -atc_priority(oid)

    sorted_ofs = sorted(of_ids, key=sort_key)
    # Étalement linéaire dans la zone négociable
    return {
        oid: freeze_end_day + (idx * width) // max(1, n)
        for idx, oid in enumerate(sorted_ofs)
    }
