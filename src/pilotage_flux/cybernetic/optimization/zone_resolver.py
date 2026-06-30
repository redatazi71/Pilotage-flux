"""V12.2 — Résolveur de zone négociable.

À tout instant `t` du planning, les OFs et candidats se distribuent
en 3 zones temporelles :

  - **Zone gelée** [t, t + freeze_window[       : intangible, freeze_batch_id
  - **Zone négociable** [t + freeze_window, t + horizon_forecast[ : optimisable
  - **Zone libre** [t + horizon_forecast, ∞[    : forecast V12.1

Le résolveur identifie les éléments dans la zone négociable pour
soumission à V12.2.cp_sat_dynamic.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class NegotiableZone:
    """État de la zone négociable à un instant donné."""

    reference_day: int                 # jour de référence (t)
    freeze_end_day: int                # t + freeze_window
    horizon_end_day: int               # t + horizon_forecast
    of_ids_in_zone: list[str] = field(default_factory=list)
    candidate_ids_in_zone: list[str] = field(default_factory=list)
    freeze_window_days: int = 0
    horizon_forecast_days: int = 0

    @property
    def width_days(self) -> int:
        return self.horizon_end_day - self.freeze_end_day

    @property
    def is_empty(self) -> bool:
        return (
            not self.of_ids_in_zone and not self.candidate_ids_in_zone
        )


def resolve_negotiable_zone(
    conn: sqlite3.Connection,
    *,
    reference_day: int = 0,
    horizon_start: str | None = None,
    freeze_window_days: int = 5,
    horizon_forecast_days: int = 28,
) -> NegotiableZone:
    """Identifie tous les OFs et candidats situés dans la zone négociable.

    Parameters
    ----------
    reference_day : int
        Jour logique courant. Les OFs avec scheduled_launch_day dans
        [reference_day + freeze_window_days, reference_day + horizon_forecast_days[
        sont considérés comme étant dans la zone négociable.
    horizon_start : str, optional
        Date de début de l'horizon (ISO). Si None, on lit depuis run_metadata.
    freeze_window_days : int
        Largeur de la fenêtre gelée (par défaut 5 jours conformes aux
        pratiques aérospatiales — paramétrable).
    horizon_forecast_days : int
        Limite supérieure de la zone négociable (par-delà = zone libre,
        gérée par V12.1 forecasting).
    """
    if horizon_start is None:
        row = conn.execute(
            "SELECT value FROM run_metadata WHERE key = 'horizon_start'"
        ).fetchone()
        if row is None:
            raise ValueError("horizon_start ni fourni ni présent dans run_metadata")
        horizon_start = row["value"]

    base = datetime.fromisoformat(horizon_start)
    freeze_end_day = reference_day + freeze_window_days
    horizon_end_day = reference_day + horizon_forecast_days

    # OFs : look at planned_start
    of_ids: list[str] = []
    of_rows = conn.execute(
        """
        SELECT of_id, planned_start, status
        FROM manufacturing_orders
        WHERE status NOT IN ('closed', 'cancelled')
        """
    ).fetchall()
    for r in of_rows:
        if not r["planned_start"]:
            continue
        try:
            pd = datetime.fromisoformat(r["planned_start"])
        except (ValueError, TypeError):
            continue
        day = (pd - base).days
        if freeze_end_day <= day < horizon_end_day:
            of_ids.append(r["of_id"])

    # Candidats : look at earliest_start
    cand_ids: list[str] = []
    cand_rows = conn.execute(
        """
        SELECT candidate_id, earliest_start, zone
        FROM candidate_orders
        WHERE status = 'candidate' AND zone IN ('libre', 'negociable')
        """
    ).fetchall()
    for r in cand_rows:
        if not r["earliest_start"]:
            continue
        try:
            es = datetime.fromisoformat(r["earliest_start"])
        except (ValueError, TypeError):
            continue
        day = (es - base).days
        if freeze_end_day <= day < horizon_end_day:
            cand_ids.append(r["candidate_id"])

    return NegotiableZone(
        reference_day=reference_day,
        freeze_end_day=freeze_end_day,
        horizon_end_day=horizon_end_day,
        of_ids_in_zone=of_ids,
        candidate_ids_in_zone=cand_ids,
        freeze_window_days=freeze_window_days,
        horizon_forecast_days=horizon_forecast_days,
    )
