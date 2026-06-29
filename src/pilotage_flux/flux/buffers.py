"""Tampons goulots et seuils de saturation Little-aware (L10.5).

Implémente la doctrine Drum-Buffer-Rope (Goldratt) appliquée au goulot
collectif identifié par L10.3 :

  - **Constraint buffer** : temps réservé devant le goulot pour absorber
    la variabilité amont. Dimensionné via loi de Little
    `tampon_min = throughput_min/min × cycle_time × safety_factor`.
  - **Shipping buffer** : marge temporelle entre clôture goulot et due_date.
    Hors scope L10.5 (lié à due-date logic) — paramétrable mais non câblé.
  - **Seuils Little** : saturation cible 80-90% (zone sûre) ;
    > 90% → PARTIAL_FREEZE ; > 110% → tronque.

Tous les paramètres sont data-driven dans `parameters` :
  - `p3_saturation_warn_ratio`   (default 0.80)
  - `p3_saturation_block_ratio`  (default 0.90)
  - `p3_saturation_defer_ratio`  (default 1.10)
  - `constraint_buffer_safety_factor` (default 0.15) → 15% de capacité réservée
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pilotage_flux.parameters import get_num


DEFAULT_SATURATION_WARN = 0.80
DEFAULT_SATURATION_BLOCK = 0.90
DEFAULT_SATURATION_DEFER = 1.10
DEFAULT_BUFFER_SAFETY_FACTOR = 0.15


@dataclass(frozen=True)
class SaturationLimits:
    """Seuils Little-aware pour décider l'état d'un goulot."""

    warn: float = DEFAULT_SATURATION_WARN
    block: float = DEFAULT_SATURATION_BLOCK
    defer: float = DEFAULT_SATURATION_DEFER

    def classify(self, ratio: float) -> str:
        """Renvoie 'safe' | 'warn' | 'block' | 'defer' selon le ratio
        load/capacity."""
        if ratio >= self.defer:
            return "defer"
        if ratio >= self.block:
            return "block"
        if ratio >= self.warn:
            return "warn"
        return "safe"


@dataclass(frozen=True)
class BufferSpec:
    """Tampon dimensionné via loi de Little.

    `reserved_capacity_min` = capacité (minutes) à NE PAS allouer pour
    absorber la variabilité au goulot (constraint buffer).
    """

    workstation_id: str
    raw_capacity_min: float
    safety_factor: float
    reserved_capacity_min: float

    @property
    def effective_capacity_min(self) -> float:
        return max(0.0, self.raw_capacity_min - self.reserved_capacity_min)


def get_saturation_limits(conn: sqlite3.Connection) -> SaturationLimits:
    """Lit les seuils Little depuis parameters."""
    return SaturationLimits(
        warn=float(get_num(
            conn, scope="global", scope_ref=None,
            name="p3_saturation_warn_ratio",
            default=DEFAULT_SATURATION_WARN,
        ) or DEFAULT_SATURATION_WARN),
        block=float(get_num(
            conn, scope="global", scope_ref=None,
            name="p3_saturation_block_ratio",
            default=DEFAULT_SATURATION_BLOCK,
        ) or DEFAULT_SATURATION_BLOCK),
        defer=float(get_num(
            conn, scope="global", scope_ref=None,
            name="p3_saturation_defer_ratio",
            default=DEFAULT_SATURATION_DEFER,
        ) or DEFAULT_SATURATION_DEFER),
    )


def get_safety_factor(conn: sqlite3.Connection) -> float:
    val = get_num(
        conn, scope="global", scope_ref=None,
        name="constraint_buffer_safety_factor",
        default=DEFAULT_BUFFER_SAFETY_FACTOR,
    )
    return float(val) if val is not None else DEFAULT_BUFFER_SAFETY_FACTOR


def little_buffer_for_bottleneck(
    workstation_id: str,
    raw_capacity_min: float,
    safety_factor: float,
) -> BufferSpec:
    """Calcule le tampon constraint pour un goulot via loi de Little.

    Modèle simplifié : on réserve `safety_factor%` de la capacité brute pour
    absorber la variabilité.
    """
    reserved = max(0.0, raw_capacity_min * safety_factor)
    return BufferSpec(
        workstation_id=workstation_id,
        raw_capacity_min=raw_capacity_min,
        safety_factor=safety_factor,
        reserved_capacity_min=reserved,
    )


def apply_buffer_to_capacity(
    raw_capacity_min: float,
    is_bottleneck: bool,
    safety_factor: float,
) -> float:
    """Renvoie la capacité effective : raw si non-goulot, raw × (1 - safety)
    si goulot."""
    if not is_bottleneck:
        return raw_capacity_min
    return max(0.0, raw_capacity_min * (1.0 - safety_factor))
