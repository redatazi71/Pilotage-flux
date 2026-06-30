"""Périodes territoriales des 3 zones du BCE (Goldilocks composant #2).

Le cadrage v1.3 (section 3.9.1) fixe les périodes par défaut :
  - Zone gelée    : 2 semaines  (14 jours)
  - Zone négociable: 10 semaines (70 jours)
  - Zone libre    : 9 mois     (270 jours)
  - Total couvert : 12 mois    (354 jours, alignement annuel)

Ces valeurs sont **paramétrables** via la table `parameters` (scope
global) avec les noms :
  - zone_gelee_period_days
  - zone_negociable_period_days
  - zone_libre_period_days

Par défaut, si les paramètres n'existent pas en base, on renvoie les
valeurs canoniques 14/70/270.

API minimale :
  - DEFAULT_PERIODS : NamedTuple immuable des valeurs canoniques
  - get_zone_periods(conn) -> ZonePeriods : lit les paramètres ou
    renvoie les defaults
  - seed_default_zone_periods(conn) -> int : pose les 3 valeurs dans
    parameters si absentes (idempotent)

La **dynamicité** de ces périodes (contraction/extension selon
nervosité, horizon insuffisant, qualité prévision) est traitée
dans le composant #3 (cybernetic/zone_dynamics.py).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pilotage_flux.parameters import get_num


# Valeurs canoniques du cadrage v1.3 §3.9.1
DEFAULT_GELEE_DAYS = 14    # 2 semaines
DEFAULT_NEGOCIABLE_DAYS = 70   # 10 semaines
DEFAULT_LIBRE_DAYS = 270   # 9 mois (mois ≈ 30j)


@dataclass(frozen=True)
class ZonePeriods:
    """Périodes territoriales courantes des 3 zones BCE.

    Toutes en JOURS.
    """
    gelee_days: int
    negociable_days: int
    libre_days: int

    @property
    def total_days(self) -> int:
        """Couverture totale = somme des 3 zones."""
        return self.gelee_days + self.negociable_days + self.libre_days

    @property
    def freeze_end_day(self) -> int:
        """Jour de fin de la zone gelée (= début zone négociable)."""
        return self.gelee_days

    @property
    def negociable_end_day(self) -> int:
        """Jour de fin de la zone négociable (= début zone libre)."""
        return self.gelee_days + self.negociable_days


DEFAULT_PERIODS = ZonePeriods(
    gelee_days=DEFAULT_GELEE_DAYS,
    negociable_days=DEFAULT_NEGOCIABLE_DAYS,
    libre_days=DEFAULT_LIBRE_DAYS,
)


def get_zone_periods(conn: sqlite3.Connection) -> ZonePeriods:
    """Renvoie les périodes courantes (paramètres ou defaults).

    Lit `zone_gelee_period_days`, `zone_negociable_period_days`,
    `zone_libre_period_days` dans `parameters` (scope='global').
    Si un paramètre est absent, utilise sa valeur canonique.
    """
    g = get_num(
        conn, scope="global", scope_ref=None,
        name="zone_gelee_period_days", default=DEFAULT_GELEE_DAYS,
    )
    n = get_num(
        conn, scope="global", scope_ref=None,
        name="zone_negociable_period_days", default=DEFAULT_NEGOCIABLE_DAYS,
    )
    l = get_num(
        conn, scope="global", scope_ref=None,
        name="zone_libre_period_days", default=DEFAULT_LIBRE_DAYS,
    )
    return ZonePeriods(
        gelee_days=int(g) if g is not None else DEFAULT_GELEE_DAYS,
        negociable_days=int(n) if n is not None else DEFAULT_NEGOCIABLE_DAYS,
        libre_days=int(l) if l is not None else DEFAULT_LIBRE_DAYS,
    )


def seed_default_zone_periods(conn: sqlite3.Connection) -> int:
    """Pose les 3 valeurs canoniques dans `parameters` si elles n'existent
    pas (idempotent). Renvoie le nombre de paramètres insérés.

    Utilisé au bootstrap des scénarios Goldilocks pour s'assurer que
    les périodes BCE sont configurées dans la base.
    """
    inserted = 0
    for name, value in (
        ("zone_gelee_period_days", DEFAULT_GELEE_DAYS),
        ("zone_negociable_period_days", DEFAULT_NEGOCIABLE_DAYS),
        ("zone_libre_period_days", DEFAULT_LIBRE_DAYS),
    ):
        existing = conn.execute(
            "SELECT 1 FROM parameters WHERE scope='global' "
            "AND scope_ref IS NULL AND name=? "
            "AND (valid_to IS NULL OR valid_to > datetime('now')) LIMIT 1",
            (name,),
        ).fetchone()
        if existing is not None:
            continue
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES ('global', NULL, ?, ?)",
            (name, float(value)),
        )
        inserted += 1
    return inserted
