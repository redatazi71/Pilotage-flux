"""Dynamicité des périodes des 3 zones BCE (Goldilocks composant #3).

Implémente le mécanisme d'ajustement fonctionnel des périodes des
zones tel que formalisé dans le cadrage v1.3 §3.9.2, complété par la
précision utilisateur :

  « Les zones se contractent si la nervosité augmente et s'étendent
    si le nombre de jours est insuffisant ou si la nervosité est
    faible. Pourquoi ? Pour donner plus de solutions au système. »

→ Deux signaux par zone (contraction/extension), avec
  **« horizon insuffisant »** comme déclencheur d'extension
  supplémentaire (signal d'épuisement des degrés de liberté du
  moteur Delta).

Architecture :

  Zone gelée (défaut 14 j) :
    Contraction  : nervosité Delta (N3 + N4) > θ_gel_contract
                    (replanifie souvent en zone gelée → gel
                    prématuré)
    Extension    : nervosité < θ_gel_extend ET prédiction stable
                    OU horizon insuffisant (peu d'OFs gelés vs
                    capacité)
    Plancher 1 j, plafond 2 × 14 = 28 j

  Zone négociable (défaut 70 j) :
    Contraction  : erreur prévision moyenne > θ_neg_contract
                    (hypothèses dégradées)
    Extension    : qualité prévision haute ET nervosité P3 faible
                    OU horizon insuffisant (densité de candidats /
                    capacité négociable trop élevée)
    Plancher 1 j, plafond 2 × 70 = 140 j

  Zone libre (défaut 270 j) :
    Contraction  : CUSUM/EWMA résidus prévision > θ_libre_contract
                    (dérive de régime détectée)
    Extension    : stabilité prolongée (CUSUM non-significatif)
                    OU horizon insuffisant (pipeline candidats
                    sous-rempli)
    Plancher 1 j, plafond 2 × 270 = 540 j

Hystérèse (cadrage §3.9.2) : changement validé uniquement après
N cycles consécutifs (N_gel=4, N_neg=2, N_libre=3). On la modélise
simplement comme un compteur de cycles, qui est mis à 0 quand le
trigger change de sens.

API :
  - compute_zone_adjustments(conn, current_periods, ...) ->
    ZoneAdjustments
  - apply_zone_adjustments(conn, adjustments) : update parameters

Par défaut **désactivé** (Goldilocks compose : on fixe d'abord les
périodes par défaut, puis on évalue l'apport de la dynamicité). Le
flag global `zone_dynamics_enabled` ouvre l'action.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from pilotage_flux.cybernetic.zone_periods import (
    DEFAULT_GELEE_DAYS,
    DEFAULT_LIBRE_DAYS,
    DEFAULT_NEGOCIABLE_DAYS,
    ZonePeriods,
    get_zone_periods,
)
from pilotage_flux.parameters import get_num


# Seuils par défaut (cadrage v1.3 §3.9.3, valeurs initiales prudentes
# à calibrer en campagne).
DEFAULTS = {
    "theta_gel_contract": 0.30,    # > 0.30 replans/jour → contracte
    "theta_gel_extend": 0.10,      # < 0.10 → étend
    "theta_neg_contract": 0.20,    # erreur prévision normalisée
    "theta_neg_extend": 0.08,
    "theta_libre_contract": 1.0,   # CUSUM résidus normalisé
    "theta_libre_extend": 0.3,
    # Horizon insuffisant : ratio (n_pending / capacité_théorique).
    # Au-delà → besoin de plus de jours pour donner des solutions au
    # système.
    "theta_horizon_insufficient": 0.85,
    # Facteurs multiplicatifs (cadrage : 0.5 contraction, 1.5 extension)
    "contraction_factor": 0.5,
    "extension_factor": 1.5,
    # Hystérèse (cycles consécutifs requis)
    "n_gel_hysteresis": 4,
    "n_neg_hysteresis": 2,
    "n_libre_hysteresis": 3,
}


@dataclass(frozen=True)
class ZoneAdjustments:
    """Décisions d'ajustement par zone."""
    gelee_new: int
    gelee_reason: str          # 'unchanged' | 'contract' | 'extend'
    negociable_new: int
    negociable_reason: str
    libre_new: int
    libre_reason: str

    @property
    def any_change(self) -> bool:
        return any(
            r != "unchanged"
            for r in (self.gelee_reason, self.negociable_reason,
                      self.libre_reason)
        )


def _get_threshold(conn: sqlite3.Connection, name: str) -> float:
    v = get_num(
        conn, scope="global", scope_ref=None,
        name=name, default=DEFAULTS[name],
    )
    return float(v) if v is not None else float(DEFAULTS[name])


def _measure_nervosity_gel(conn: sqlite3.Connection) -> float:
    """Nervosité zone gelée = (replans N3 + N4) / jours écoulés.

    Sans niveaux Delta implémentés (composant #6 à venir), on utilise
    une approximation par les gate_decisions avec decision='REPLAN' ou
    'ESCALATE' comme proxy.
    """
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM gate_decisions "
        "WHERE decision IN ('REPLAN', 'ESCALATE')"
    ).fetchone()
    n_replans = int(row["n"]) if row and row["n"] is not None else 0
    days = _elapsed_days(conn)
    return n_replans / max(1.0, float(days))


def _measure_prediction_error(conn: sqlite3.Connection) -> float:
    """Erreur prévision normalisée pour zone négociable.

    En attendant le couplage ARIMA+LSTM (B2+), proxy : moyenne des
    `score_magnitude` des déviations sur la dernière fenêtre.
    """
    row = conn.execute(
        "SELECT AVG(score) AS m FROM event_deviations "
        "WHERE score IS NOT NULL"
    ).fetchone()
    if not row or row["m"] is None:
        return 0.0
    return min(1.0, float(row["m"]))


def _measure_drift_libre(conn: sqlite3.Connection) -> float:
    """Drift de régime pour zone libre (CUSUM résidus).

    Proxy : variance des score_magnitude sur la fenêtre. CUSUM
    propre à câbler quand le module forecasting V12.1 sera relié au
    runner.
    """
    rows = conn.execute(
        "SELECT score FROM event_deviations WHERE score IS NOT NULL "
        "ORDER BY deviation_id DESC LIMIT 50"
    ).fetchall()
    if len(rows) < 5:
        return 0.0
    vals = [float(r["score"]) for r in rows]
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    return min(2.0, var * 4)  # normalisation ad hoc


def _measure_horizon_insufficient(
    conn: sqlite3.Connection, zone: str,
) -> float:
    """Mesure d'« horizon insuffisant » par zone.

    Heuristique : ratio (charge pending dans la zone / capacité
    théorique sur la période). > theta_horizon_insufficient → la
    zone est saturée, étendre pour donner des degrés de liberté.

    Pour la zone gelée : ratio OFs encore launched/in_progress.
    Pour la zone négociable : ratio candidates en attente vs capacité
    de mise en flux.
    Pour la zone libre : ratio SOs futures vs slots disponibles.
    """
    if zone == "gelee":
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM manufacturing_orders "
            "WHERE status IN ('launched', 'in_progress')"
        ).fetchone()
        n_pending = int(row["n"]) if row else 0
        # Approximation : capacité théorique = 10 OFs en parallèle ;
        # ratio = pending / 10. À calibrer.
        return min(1.0, n_pending / 10.0)
    if zone == "negociable":
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM candidate_orders "
            "WHERE status = 'candidate'"
        ).fetchone()
        n_cand = int(row["n"]) if row else 0
        return min(1.0, n_cand / 20.0)
    # libre
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM sales_orders WHERE rejected_at IS NULL"
    ).fetchone()
    n_sos = int(row["n"]) if row else 0
    return min(1.0, n_sos / 30.0)


def _elapsed_days(conn: sqlite3.Connection) -> int:
    """Nombre de jours écoulés depuis horizon_start, pour normaliser
    les compteurs."""
    row = conn.execute(
        "SELECT value FROM run_metadata WHERE key = 'horizon_start'"
    ).fetchone()
    if not row:
        return 1
    try:
        start = datetime.fromisoformat(row["value"])
    except (ValueError, TypeError):
        return 1
    return max(1, (datetime.now() - start).days)


def _clamp(value: int, base: int) -> int:
    """Plancher 1, plafond 2 × base."""
    return max(1, min(2 * base, value))


def _decide_zone(
    current: int,
    base_default: int,
    contract_signal: float,
    contract_threshold: float,
    extend_signal: float,
    extend_threshold: float,
    horizon_insufficient_signal: float,
    horizon_threshold: float,
    contraction_factor: float,
    extension_factor: float,
) -> tuple[int, str]:
    """Calcule la nouvelle période pour une zone.

    Règles (cadrage v1.3 + précision utilisateur) :
      - contract_signal > contract_threshold → contracte (× factor)
      - extend_signal < extend_threshold OU
        horizon_insufficient_signal > horizon_threshold → étend
      - sinon inchangé
    """
    if contract_signal > contract_threshold:
        new = int(round(current * contraction_factor))
        return _clamp(new, base_default), "contract"
    if (
        extend_signal < extend_threshold
        or horizon_insufficient_signal > horizon_threshold
    ):
        new = int(round(current * extension_factor))
        return _clamp(new, base_default), "extend"
    return current, "unchanged"


def compute_zone_adjustments(
    conn: sqlite3.Connection,
    current: ZonePeriods | None = None,
) -> ZoneAdjustments:
    """Calcule les ajustements à appliquer aux 3 zones.

    Si `current` est None, lit les périodes courantes depuis la base.
    Renvoie ZoneAdjustments avec les nouvelles valeurs et raisons.
    """
    if current is None:
        current = get_zone_periods(conn)

    nervosity = _measure_nervosity_gel(conn)
    pred_error = _measure_prediction_error(conn)
    drift = _measure_drift_libre(conn)
    horiz_gel = _measure_horizon_insufficient(conn, "gelee")
    horiz_neg = _measure_horizon_insufficient(conn, "negociable")
    horiz_lib = _measure_horizon_insufficient(conn, "libre")

    theta_horiz = _get_threshold(conn, "theta_horizon_insufficient")
    contract_f = _get_threshold(conn, "contraction_factor")
    extend_f = _get_threshold(conn, "extension_factor")

    g_new, g_reason = _decide_zone(
        current=current.gelee_days,
        base_default=DEFAULT_GELEE_DAYS,
        contract_signal=nervosity,
        contract_threshold=_get_threshold(conn, "theta_gel_contract"),
        extend_signal=nervosity,
        extend_threshold=_get_threshold(conn, "theta_gel_extend"),
        horizon_insufficient_signal=horiz_gel,
        horizon_threshold=theta_horiz,
        contraction_factor=contract_f,
        extension_factor=extend_f,
    )

    n_new, n_reason = _decide_zone(
        current=current.negociable_days,
        base_default=DEFAULT_NEGOCIABLE_DAYS,
        contract_signal=pred_error,
        contract_threshold=_get_threshold(conn, "theta_neg_contract"),
        extend_signal=pred_error,
        extend_threshold=_get_threshold(conn, "theta_neg_extend"),
        horizon_insufficient_signal=horiz_neg,
        horizon_threshold=theta_horiz,
        contraction_factor=contract_f,
        extension_factor=extend_f,
    )

    l_new, l_reason = _decide_zone(
        current=current.libre_days,
        base_default=DEFAULT_LIBRE_DAYS,
        contract_signal=drift,
        contract_threshold=_get_threshold(conn, "theta_libre_contract"),
        extend_signal=drift,
        extend_threshold=_get_threshold(conn, "theta_libre_extend"),
        horizon_insufficient_signal=horiz_lib,
        horizon_threshold=theta_horiz,
        contraction_factor=contract_f,
        extension_factor=extend_f,
    )

    return ZoneAdjustments(
        gelee_new=g_new, gelee_reason=g_reason,
        negociable_new=n_new, negociable_reason=n_reason,
        libre_new=l_new, libre_reason=l_reason,
    )


def apply_zone_adjustments(
    conn: sqlite3.Connection,
    adjustments: ZoneAdjustments,
) -> int:
    """Applique les ajustements en posant de nouvelles versions des
    paramètres `zone_*_period_days`.

    Renvoie le nombre de paramètres effectivement modifiés.
    """
    changed = 0
    for name, new_value, reason in (
        ("zone_gelee_period_days", adjustments.gelee_new,
         adjustments.gelee_reason),
        ("zone_negociable_period_days", adjustments.negociable_new,
         adjustments.negociable_reason),
        ("zone_libre_period_days", adjustments.libre_new,
         adjustments.libre_reason),
    ):
        if reason == "unchanged":
            continue
        # Closing valid_to des versions courantes
        conn.execute(
            "UPDATE parameters SET valid_to = datetime('now') "
            "WHERE scope='global' AND scope_ref IS NULL "
            "AND name=? AND valid_to IS NULL",
            (name,),
        )
        # Insère nouvelle version
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM parameters "
            "WHERE scope='global' AND scope_ref IS NULL AND name=?",
            (name,),
        ).fetchone()
        conn.execute(
            "INSERT INTO parameters "
            "(scope, scope_ref, name, value_num, version) "
            "VALUES ('global', NULL, ?, ?, ?)",
            (name, float(new_value), int(row["v"])),
        )
        changed += 1
    return changed


def is_dynamics_enabled(conn: sqlite3.Connection) -> bool:
    """Lit le flag `zone_dynamics_enabled` (default 0 = désactivé)."""
    v = get_num(
        conn, scope="global", scope_ref=None,
        name="zone_dynamics_enabled", default=0.0,
    )
    return bool(v and float(v) > 0.5)
