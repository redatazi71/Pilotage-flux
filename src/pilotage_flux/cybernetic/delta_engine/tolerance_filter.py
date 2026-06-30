"""Filtre dual des tolérances — formalisation B.2.

Référence cadrage §7bis.4 :

    « niveau_action = f(score_magnitude, fréquence_fenêtre, latence)
      où niveau_action ∈ {informer, surveiller, corriger_local,
      replanifier_local, escalader, replanifier_global}. Les
      coefficients sont des paramètres SQLite. »

Cette couche **encapsule et formalise** le module existant
`events_v3/dual_tolerance.py` :

  - regroupe les 7 paramètres (5 seuils + window + latence) en un
    **profil nommé versionnable** (ToleranceProfile) — analogue
    aux weight_versions du MACRS A.4 ;
  - mappe la sortie text `action_level` du module historique sur
    le `niveau_code` (L1..L6) du moteur Delta B.1 ;
  - expose un helper end-to-end `evaluate_and_decide` qui chaîne
    filtre dual → delta_decision, prêt à être consommé par B.3
    (wiring MACRS Couche 2 → moteur Delta).

Profils canoniques fournis :
  - DEFAULT      : seuils 0.20 / 0.40 / 0.60 / 0.80 / 1.20
  - CONSERVATIVE : seuils plus hauts (tolérance large) +
                   latence 30 min — moins de réaction
  - REACTIVE     : seuils plus bas + latence 0 — plus de réaction
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pilotage_flux.events_v3.dual_tolerance import (
    ACTION_CORRECT_LOCAL,
    ACTION_ESCALATE,
    ACTION_INFORM,
    ACTION_REPLAN_GLOBAL,
    ACTION_REPLAN_LOCAL,
    ACTION_WATCH,
    DEFAULT_LATENCY_MINUTES,
    DEFAULT_THRESHOLDS,
    DEFAULT_WINDOW_HOURS,
    ToleranceDecision,
    evaluate_dual_tolerance,
)
from pilotage_flux.cybernetic.delta_engine.decisions import (
    create_delta_decision,
)
from pilotage_flux.cybernetic.delta_engine.levels import (
    L_CORRIGER_LOCAL,
    L_ESCALADER,
    L_INFORMER,
    L_REPLANIFIER_GLOBAL,
    L_REPLANIFIER_LOCAL,
    L_SURVEILLER,
)


# ---------------------------------------------------------------------
# Mapping action_level (legacy CDC) → niveau_code (moteur Delta B.1)
# ---------------------------------------------------------------------

ACTION_TO_NIVEAU: dict[str, str] = {
    ACTION_INFORM:         L_INFORMER,
    ACTION_WATCH:          L_SURVEILLER,
    ACTION_CORRECT_LOCAL:  L_CORRIGER_LOCAL,
    ACTION_REPLAN_LOCAL:   L_REPLANIFIER_LOCAL,
    ACTION_ESCALATE:       L_ESCALADER,
    ACTION_REPLAN_GLOBAL:  L_REPLANIFIER_GLOBAL,
}


def map_action_to_niveau(action_level: str) -> str:
    """Convertit un action_level historique (events_v3) vers le
    niveau_code unifié L1..L6 (delta_engine B.1).

    Lève ValueError si l'action n'est pas reconnue.
    """
    if action_level not in ACTION_TO_NIVEAU:
        raise ValueError(
            f"action_level inconnu : {action_level} "
            f"(attendus {tuple(ACTION_TO_NIVEAU)})"
        )
    return ACTION_TO_NIVEAU[action_level]


# ---------------------------------------------------------------------
# Profil de tolérance versionné
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class ToleranceProfile:
    """Bundle nommé des 7 paramètres du filtre dual.

    Persisté dans `parameters` (scope='global') via apply_profile.
    """
    label: str
    threshold_watch: float
    threshold_correct_local: float
    threshold_replan_local: float
    threshold_escalate: float
    threshold_replan_global: float
    window_hours: int
    latency_minutes: int


DEFAULT_PROFILE = ToleranceProfile(
    label="default",
    threshold_watch=DEFAULT_THRESHOLDS["tolerance_threshold_watch"],
    threshold_correct_local=DEFAULT_THRESHOLDS["tolerance_threshold_correct_local"],
    threshold_replan_local=DEFAULT_THRESHOLDS["tolerance_threshold_replan_local"],
    threshold_escalate=DEFAULT_THRESHOLDS["tolerance_threshold_escalate"],
    threshold_replan_global=DEFAULT_THRESHOLDS["tolerance_threshold_replan_global"],
    window_hours=DEFAULT_WINDOW_HOURS,
    latency_minutes=DEFAULT_LATENCY_MINUTES,
)

# Tolérance large : le système favorise l'absorption (N1) et garde
# l'escalade (N3/N4) pour les événements vraiment exceptionnels. C'est
# l'esprit doctrinal de la BCE qui doit ABSORBER plus et REPLANIFIER
# moins. Profil utilisé par les pilotages BCE.
#
# Avec ces seuils, un score_combined typique 0.6-0.8 (qui correspond
# à un hazard standard sur 1 occurrence) tombe en informer/surveiller
# (L1/L2 = N1) au lieu de replanifier.
CONSERVATIVE_PROFILE = ToleranceProfile(
    label="conservative",
    threshold_watch=0.50,
    threshold_correct_local=1.00,
    threshold_replan_local=1.50,
    threshold_escalate=2.00,
    threshold_replan_global=3.00,
    window_hours=48,
    latency_minutes=30,
)

# Tolérance étroite : déclenche vite, plus de N3/N4. Utile pour
# pilotages réactifs sans MACRS (qui ne peuvent pas absorber).
REACTIVE_PROFILE = ToleranceProfile(
    label="reactive",
    threshold_watch=0.10,
    threshold_correct_local=0.25,
    threshold_replan_local=0.40,
    threshold_escalate=0.60,
    threshold_replan_global=0.90,
    window_hours=12,
    latency_minutes=0,
)

PROFILES_BY_LABEL: dict[str, ToleranceProfile] = {
    DEFAULT_PROFILE.label: DEFAULT_PROFILE,
    CONSERVATIVE_PROFILE.label: CONSERVATIVE_PROFILE,
    REACTIVE_PROFILE.label: REACTIVE_PROFILE,
}


def apply_tolerance_profile(
    conn: sqlite3.Connection, profile: ToleranceProfile,
) -> None:
    """Persiste les 7 paramètres du profil dans `parameters`.

    Idempotent par construction : SQLite UPSERT via INSERT OR REPLACE
    sur (scope, scope_ref, name) — on close d'abord l'ancienne version
    en valid_to=now puis on insère la nouvelle pour respecter le
    versioning.
    """
    pairs: tuple[tuple[str, float], ...] = (
        ("tolerance_threshold_watch", profile.threshold_watch),
        ("tolerance_threshold_correct_local", profile.threshold_correct_local),
        ("tolerance_threshold_replan_local", profile.threshold_replan_local),
        ("tolerance_threshold_escalate", profile.threshold_escalate),
        ("tolerance_threshold_replan_global", profile.threshold_replan_global),
        ("tolerance_window_hours", float(profile.window_hours)),
        ("tolerance_latency_minutes", float(profile.latency_minutes)),
    )
    for name, value in pairs:
        # Close l'ancienne version courante (s'il y en a une)
        conn.execute(
            "UPDATE parameters SET valid_to = datetime('now') "
            "WHERE scope='global' AND scope_ref IS NULL "
            "AND name = ? AND valid_to IS NULL",
            (name,),
        )
        # Calcule la nouvelle version
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM parameters "
            "WHERE scope='global' AND scope_ref IS NULL AND name = ?",
            (name,),
        ).fetchone()
        conn.execute(
            "INSERT INTO parameters "
            "(scope, scope_ref, name, value_num, value_text, version) "
            "VALUES ('global', NULL, ?, ?, ?, ?)",
            (name, float(value), profile.label, int(row["v"])),
        )


def get_current_tolerance_profile(
    conn: sqlite3.Connection,
) -> ToleranceProfile:
    """Reconstruit le profil courant en lisant `parameters`.

    Le `label` est lu sur value_text de threshold_watch (les 7 params
    portent en principe la même étiquette de profil). Si aucun
    paramétrage n'est posé, renvoie DEFAULT_PROFILE.
    """
    def _read(name: str, fallback: float) -> tuple[float, str | None]:
        row = conn.execute(
            "SELECT value_num, value_text FROM parameters "
            "WHERE scope='global' AND scope_ref IS NULL AND name = ? "
            "AND valid_to IS NULL "
            "ORDER BY version DESC LIMIT 1",
            (name,),
        ).fetchone()
        if row is None:
            return fallback, None
        return (
            float(row["value_num"]) if row["value_num"] is not None else fallback,
            row["value_text"],
        )

    th_watch, lbl = _read(
        "tolerance_threshold_watch", DEFAULT_PROFILE.threshold_watch,
    )
    th_cor, _ = _read(
        "tolerance_threshold_correct_local",
        DEFAULT_PROFILE.threshold_correct_local,
    )
    th_rep, _ = _read(
        "tolerance_threshold_replan_local",
        DEFAULT_PROFILE.threshold_replan_local,
    )
    th_esc, _ = _read(
        "tolerance_threshold_escalate", DEFAULT_PROFILE.threshold_escalate,
    )
    th_gbl, _ = _read(
        "tolerance_threshold_replan_global",
        DEFAULT_PROFILE.threshold_replan_global,
    )
    win, _ = _read(
        "tolerance_window_hours", float(DEFAULT_PROFILE.window_hours),
    )
    lat, _ = _read(
        "tolerance_latency_minutes", float(DEFAULT_PROFILE.latency_minutes),
    )
    return ToleranceProfile(
        label=lbl if lbl is not None else DEFAULT_PROFILE.label,
        threshold_watch=th_watch,
        threshold_correct_local=th_cor,
        threshold_replan_local=th_rep,
        threshold_escalate=th_esc,
        threshold_replan_global=th_gbl,
        window_hours=int(win),
        latency_minutes=int(lat),
    )


# ---------------------------------------------------------------------
# End-to-end : déviation → filtre dual → delta_decision (B.1)
# ---------------------------------------------------------------------


def evaluate_and_decide(
    conn: sqlite3.Connection,
    deviation_id: int,
    *,
    decided_at: str,
    racine_id: str | None = None,
    categorie_code: str | None = None,
    actor: str | None = "auto:delta_engine",
) -> tuple[ToleranceDecision, int]:
    """Chaîne complète filtre dual → décision niveau Delta.

    1. Évalue le filtre dual (events_v3.evaluate_dual_tolerance,
       idempotent).
    2. Mappe `action_level` legacy → `niveau_code` L1..L6.
    3. Crée une `delta_decision` reliant déviation, niveau Delta,
       attribution causale (racine, catégorie — optionnels en B.2,
       remplis en B.3 par le wiring MACRS), score_magnitude et
       fréquence observée.

    Renvoie (ToleranceDecision, delta_decision_id).
    """
    tol = evaluate_dual_tolerance(conn, deviation_id)
    niveau_code = map_action_to_niveau(tol.action_level)
    delta_id = create_delta_decision(
        conn,
        niveau_code=niveau_code,
        decided_at=decided_at,
        deviation_id=deviation_id,
        racine_id=racine_id,
        categorie_code=categorie_code,
        score_magnitude=tol.score_combined,
        frequency=float(tol.frequency_in_window),
        explanation=(
            f"filtre_dual: action={tol.action_level}, "
            f"score_mag={tol.score_magnitude:.3f}, "
            f"freq={tol.frequency_in_window}, "
            f"score_comb={tol.score_combined:.3f}"
        ),
        actor=actor,
    )
    return tol, delta_id
