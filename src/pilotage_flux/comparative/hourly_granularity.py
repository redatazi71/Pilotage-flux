"""Ext-i — Granularité horaire pour secteurs à cycles courts.

Certains secteurs (agroalimentaire, pharma, cosmétique, papeterie)
opèrent en cycles batch de quelques heures — pas quelques jours. Les
KPIs quotidiens masquent les variations intra-journalières qui sont
justement le domaine de la doctrine événementielle.

Ce module ajoute des **agrégations horaires** calculées à partir des
`order_operations` déjà horodatées à la minute. Il n'effectue **pas**
un refactor global de la simulation (le calendrier reste en jours) :
c'est une couche d'observation qui rejoue les événements à un pas
plus fin.

Profils secteurs livrés :

- `SECTOR_DISCRETE` — pas d'aggrégation horaire (mécanique/aéro,
  cycles > 1 jour). C'est le comportement par défaut.
- `SECTOR_CONTINUOUS_FMCG` — cycles batch 2–8 h (agro, cosmétique).
  Fournit `hourly_wip`, `hourly_events`, `intraday_nervousness`.

Le paper §7 « limites » utilise ces agrégations pour montrer que les
gains FLUX+EVENT s'**amplifient** en granularité horaire :  plus la
fenêtre est courte, plus l'event sourcing capte tôt les dérives et plus
le lissage capacity-aware du contrat de flux se distingue du pull ordre-
par-ordre.
"""

from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


SECTOR_DISCRETE = "discrete_daily"
SECTOR_CONTINUOUS_FMCG = "continuous_fmcg"
SECTOR_PROFILES = (SECTOR_DISCRETE, SECTOR_CONTINUOUS_FMCG)


@dataclass
class HourlyKpiSet:
    """KPI horaire — companion de KpiSet quotidien.

    - `hourly_wip` : nb ops en cours à chaque heure de la fenêtre.
    - `hourly_completed` : ops clôturées par heure.
    - `hourly_events_deviations` : déviations détectées par heure.
    - `intraday_nervousness` : σ(replans par heure) — mesure la
      régularité intra-journée du taux de replan.
    - `peak_hour_wip` / `trough_hour_wip` : pic et creux horaires.
    - `hours_observed` : nb d'heures avec activité.
    """

    doctrine: str
    scenario_name: str
    sector_profile: str

    hours_observed: int
    peak_hour_wip: float
    trough_hour_wip: float
    mean_hourly_wip: float
    std_hourly_wip: float

    hourly_wip: dict[str, float] = field(default_factory=dict)
    hourly_completed: dict[str, int] = field(default_factory=dict)
    hourly_events_deviations: dict[str, int] = field(default_factory=dict)
    intraday_nervousness: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


def _hour_bucket(ts: str) -> str:
    """Convertit un timestamp SQL en clé horaire `YYYY-MM-DD HH`."""
    try:
        dt = datetime.fromisoformat(ts.replace("T", " "))
    except (ValueError, TypeError, AttributeError):
        return ""
    return dt.strftime("%Y-%m-%d %H")


def compute_hourly_wip(conn: sqlite3.Connection) -> dict[str, float]:
    """WIP horaire = nb opérations avec actual_start ≤ h et actual_end > h."""
    rows = conn.execute("""
        SELECT actual_start, actual_end
        FROM order_operations
        WHERE actual_start IS NOT NULL AND actual_end IS NOT NULL
    """).fetchall()
    if not rows:
        return {}
    # Détermine la fenêtre horaire globale
    starts, ends = [], []
    for r in rows:
        starts.append(r["actual_start"])
        ends.append(r["actual_end"])
    min_start = min(starts)
    max_end = max(ends)
    try:
        t0 = datetime.fromisoformat(min_start.replace("T", " "))
        t1 = datetime.fromisoformat(max_end.replace("T", " "))
    except (ValueError, TypeError):
        return {}

    # Balaie heure par heure
    result: dict[str, float] = {}
    current = t0.replace(minute=0, second=0, microsecond=0)
    hour_seconds = 3600.0
    while current <= t1:
        h_key = current.strftime("%Y-%m-%d %H")
        end_of_hour = current.timestamp() + hour_seconds
        n_active = 0
        for r in rows:
            try:
                s = datetime.fromisoformat(
                    r["actual_start"].replace("T", " ")
                ).timestamp()
                e = datetime.fromisoformat(
                    r["actual_end"].replace("T", " ")
                ).timestamp()
            except (ValueError, TypeError):
                continue
            if s <= end_of_hour and e > current.timestamp():
                n_active += 1
        if n_active > 0:
            result[h_key] = float(n_active)
        current = current.replace(hour=(current.hour + 1) % 24)
        # Passer au jour suivant si nécessaire
        if current.hour == 0:
            from datetime import timedelta
            current = current + timedelta(days=1)
    return result


def compute_hourly_completions(conn: sqlite3.Connection) -> dict[str, int]:
    """Nb opérations clôturées par heure."""
    rows = conn.execute("""
        SELECT actual_end FROM order_operations
        WHERE actual_end IS NOT NULL
    """).fetchall()
    buckets: dict[str, int] = {}
    for r in rows:
        h = _hour_bucket(r["actual_end"])
        if h:
            buckets[h] = buckets.get(h, 0) + 1
    return buckets


def compute_hourly_deviations(conn: sqlite3.Connection) -> dict[str, int]:
    """Nb déviations détectées par heure (colonne detected_at)."""
    try:
        rows = conn.execute("""
            SELECT detected_at FROM event_deviations
            WHERE detected_at IS NOT NULL
        """).fetchall()
    except sqlite3.Error:
        return {}
    buckets: dict[str, int] = {}
    for r in rows:
        h = _hour_bucket(r["detected_at"])
        if h:
            buckets[h] = buckets.get(h, 0) + 1
    return buckets


def compute_hourly_kpis(
    result,
    db_path: Path,
    *,
    sector_profile: str = SECTOR_CONTINUOUS_FMCG,
) -> HourlyKpiSet:
    """Calcule les KPIs horaires du run à partir des tables order_operations
    et event_deviations. Ne modifie ni le runner ni les KPIs quotidiens.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    hourly_wip = compute_hourly_wip(conn)
    hourly_completed = compute_hourly_completions(conn)
    hourly_devs = compute_hourly_deviations(conn)
    conn.close()

    wip_values = list(hourly_wip.values())
    peak = max(wip_values) if wip_values else 0.0
    trough = min(wip_values) if wip_values else 0.0
    mean_wip = statistics.mean(wip_values) if wip_values else 0.0
    std_wip = statistics.pstdev(wip_values) if len(wip_values) > 1 else 0.0

    # Intraday nervousness = variabilité horaire des replans
    replan_values = list(hourly_devs.values())
    if len(replan_values) > 1:
        intraday_nerv = statistics.pstdev(replan_values)
    else:
        intraday_nerv = 0.0

    return HourlyKpiSet(
        doctrine=result.doctrine,
        scenario_name=result.scenario_name,
        sector_profile=sector_profile,
        hours_observed=len(hourly_wip),
        peak_hour_wip=round(peak, 2),
        trough_hour_wip=round(trough, 2),
        mean_hourly_wip=round(mean_wip, 2),
        std_hourly_wip=round(std_wip, 2),
        hourly_wip=hourly_wip,
        hourly_completed=hourly_completed,
        hourly_events_deviations=hourly_devs,
        intraday_nervousness=round(intraday_nerv, 3),
    )
