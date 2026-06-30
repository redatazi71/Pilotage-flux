"""Émission forecast deviation — wiring zone libre dans la chaîne BCE.

Quand un événement révèle un écart entre la **demande prévue
(forecast)** et la **demande réelle** observée sur la zone libre,
on émet une déviation dédiée à la causalité forecast, distincte
de l'émission hazard standard (C.2).

Cas canoniques :
  - HAZARD_URGENT_ORDER → R017 "Erreur de couverture" (Mat)
    Une commande client non prévue révèle directement un défaut
    de prévision.
  - HAZARD_PO_DELAY → R017 "Erreur de couverture" (Mat)
    Un retard fournisseur révèle une couverture insuffisante des
    besoins ; le forecast d'approvisionnement aurait dû être plus
    couvrant.

Cette émission **complète** l'émission hazard de C.2 sans la
remplacer : un seul événement réel peut produire plusieurs
attributions causales, ce qui est conforme au cadrage v1.3 §3.10
(les chaînes causales sont multiples par construction).

Cellules MACRS alimentées par la zone libre :
  - R002 Variation conjoncturelle (Mat / Cap / Temp)
  - R003 Saisonnalité non anticipée (Mat / Cap / Temp)
  - R017 Erreur de couverture (Mat / Op / Temp / Info / Sync)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pilotage_flux.comparative.scenario import (
    HAZARD_PO_DELAY,
    HAZARD_URGENT_ORDER,
    HazardEvent,
)


# Mapping hazard kind → (racine_id, categorie_code) ZONE LIBRE
# Distinct du mapping C.1 hazard_labels (qui cible R005, R011 pour
# ces mêmes hazards en attribution timing/appro). Ici c'est la vue
# « erreur de prévision » de la zone libre.
HAZARD_TO_FORECAST_RACINE: dict[str, tuple[str, str]] = {
    HAZARD_URGENT_ORDER:    ("R017", "Mat"),
    HAZARD_PO_DELAY:        ("R017", "Mat"),
}


# Racines doctrinalement « zone libre » : les indicateurs nervosité
# zone libre filtrent par ces racines.
ZONE_LIBRE_RACINES: tuple[str, ...] = ("R002", "R003", "R017")


@dataclass(frozen=True)
class ForecastEmissionResult:
    """Résultat de l'émission forecast d'un hazard."""
    hazard_kind: str
    racine_id: str | None
    categorie_code: str | None
    deviation_id: int | None
    delta_decision_id: int | None
    skipped_reason: str | None         # 'no_forecast_mapping' | None


def emit_forecast_deviation(
    conn: sqlite3.Connection,
    hazard: HazardEvent,
    *,
    occurred_at: str,
    decided_at: str,
    impact_score: float | None = None,
) -> ForecastEmissionResult:
    """Émet une déviation forecast si le hazard a un mapping zone libre.

    Pipeline :
      1. Cherche (racine, catégorie) dans HAZARD_TO_FORECAST_RACINE
      2. Si trouvé, crée un event_deviation distinct du C.2 standard,
         marqué d'une qualification 'forecast' pour traçabilité
      3. Appelle record_and_decide → MACRS Couche 2 + filtre dual
         + delta_decision

    Si pas de mapping (hazard non-forecast), renvoie un résultat
    skipped sans erreur.
    """
    mapping = HAZARD_TO_FORECAST_RACINE.get(hazard.kind)
    if mapping is None:
        return ForecastEmissionResult(
            hazard_kind=hazard.kind,
            racine_id=None, categorie_code=None,
            deviation_id=None, delta_decision_id=None,
            skipped_reason="no_forecast_mapping",
        )
    racine_id, categorie_code = mapping

    # Score par défaut pour forecast deviation : on prend un score
    # plus modéré que le hazard direct (la dimension forecast est
    # un signal indirect — la dérive est cumulative, pas brutale).
    score = (
        float(impact_score) if impact_score is not None else 0.45
    )
    score = max(0.0, min(1.0, score))

    # Insère event_deviation marqué 'forecast'
    cur = conn.execute(
        """
        INSERT INTO event_deviations
            (deviation_kind, delta_value, score, qualification,
             detected_at, is_absorbed)
        VALUES ('forecast_delta', 1.0, ?, 'forecast', ?, 0)
        """,
        (score, occurred_at),
    )
    dev_id = int(cur.lastrowid)

    # Import paresseux (cycle)
    from pilotage_flux.cybernetic.delta_engine.macrs_wiring import (
        record_and_decide,
    )
    cyber = record_and_decide(
        conn,
        deviation_id=dev_id,
        racine_id=racine_id,
        categorie_code=categorie_code,
        occurred_at=occurred_at,
        decided_at=decided_at,
        delay_hours=24.0,   # signal long terme par défaut
        impact_score=score,
        enqueue_if_human=True,
        actor="auto:bce_loop_zone_libre",
    )

    return ForecastEmissionResult(
        hazard_kind=hazard.kind,
        racine_id=racine_id,
        categorie_code=categorie_code,
        deviation_id=dev_id,
        delta_decision_id=cyber.delta_decision.delta_decision_id,
        skipped_reason=None,
    )


def count_zone_libre_decisions(
    conn: sqlite3.Connection,
) -> dict:
    """Distribution des delta_decisions sur les racines zone libre.

    Renvoie :
      - n_total : nb total de decisions sur R002/R003/R017
      - by_racine : dict {racine_id: count}
      - by_niveau : dict {niveau_code: count} (sur le sous-ensemble
                    zone libre)
    """
    placeholders = ",".join(["?"] * len(ZONE_LIBRE_RACINES))
    n_total = conn.execute(
        f"SELECT COUNT(*) AS n FROM delta_decisions "
        f"WHERE racine_id IN ({placeholders})",
        ZONE_LIBRE_RACINES,
    ).fetchone()
    by_racine_rows = conn.execute(
        f"SELECT racine_id, COUNT(*) AS n FROM delta_decisions "
        f"WHERE racine_id IN ({placeholders}) "
        f"GROUP BY racine_id",
        ZONE_LIBRE_RACINES,
    ).fetchall()
    by_niveau_rows = conn.execute(
        f"SELECT niveau_code, COUNT(*) AS n FROM delta_decisions "
        f"WHERE racine_id IN ({placeholders}) "
        f"GROUP BY niveau_code",
        ZONE_LIBRE_RACINES,
    ).fetchall()
    return {
        "n_total": int(n_total["n"]) if n_total else 0,
        "by_racine": {
            r["racine_id"]: int(r["n"]) for r in by_racine_rows
        },
        "by_niveau": {
            r["niveau_code"]: int(r["n"]) for r in by_niveau_rows
        },
    }
