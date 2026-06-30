"""KPIs avancés du banc cybernétique : robustesse + agilité.

Définitions doctrinales (cadrage v1.3) :

  **Robustesse** = seuil de rupture, c'est-à-dire le niveau de
  saturation R1 à partir duquel un KPI cible (typiquement OTIF)
  décroche sous un seuil critique. Plus la robustesse est élevée
  (saturation plus haute avant rupture), plus le système résiste.

  **Agilité** = temps de récupération post-perturbation, mesuré
  comme le nombre de jours simulés écoulés entre la manifestation
  d'un hazard et le retour du WIP (ou OTIF instantané) à sa bande
  nominale (typiquement bande ±10% autour du baseline pré-hazard).
  Plus l'agilité est élevée (temps court), plus le système réagit.

Ces KPIs sont calculés à partir des objets RunResult standards —
pas de modification des runners requise. La fonction de
robustesse prend un dict mappant saturation → KPI et retourne le
seuil interpolé linéairement entre les 2 points encadrant le
franchissement. La fonction d'agilité prend `daily_wip` + les
jours des hazards et retourne le temps moyen de retour à la bande.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RobustessePoint:
    """Point de mesure pour la courbe robustesse."""
    saturation: float
    kpi_value: float


@dataclass(frozen=True)
class RobustesseResult:
    """Résultat du calcul de robustesse.

    `breaking_point_saturation` : saturation à laquelle le KPI
        franchit le seuil — interpolé linéairement entre les deux
        points encadrant. None si :
        - aucun point en-dessous du seuil (système robuste sur
          toute la plage)
        - aucun point au-dessus du seuil (système jamais robuste
          → breaking_point = saturation minimale)
    `kpi_at_max_saturation` : valeur du KPI à la saturation max
        (utile pour comparer entre pilotages quand pas de rupture).
    """
    breaking_point_saturation: float | None
    kpi_threshold: float
    kpi_at_max_saturation: float
    monotone_decreasing: bool


def compute_robustesse(
    kpi_by_saturation: dict[float, float],
    *,
    kpi_threshold: float = 0.90,
) -> RobustesseResult:
    """Calcule la robustesse à partir d'une courbe (saturation → KPI).

    Sémantique : on cherche la **première** saturation (par ordre
    croissant) où le KPI passe sous `kpi_threshold`. C'est le seuil
    de rupture. Si la courbe n'est pas monotone décroissante, on le
    flague mais on calcule quand même.

    Exemples :
      kpi_by_saturation = {0.78: 1.0, 0.86: 0.95, 0.94: 0.85}
      threshold = 0.90
      → breaking_point ∈ (0.86, 0.94), interpolé linéairement
        à 0.86 + (0.94-0.86) × (0.95-0.90)/(0.95-0.85) = 0.90

    Raises ValueError si `kpi_by_saturation` est vide.
    """
    if not kpi_by_saturation:
        raise ValueError("kpi_by_saturation est vide")

    points = sorted(
        kpi_by_saturation.items(), key=lambda kv: kv[0],
    )
    saturations = [p[0] for p in points]
    kpis = [p[1] for p in points]

    # Détecte la monotonie décroissante
    monotone = all(
        kpis[i] >= kpis[i + 1] - 1e-9
        for i in range(len(kpis) - 1)
    )

    # Cherche le premier franchissement à la baisse
    breaking_point: float | None = None
    for i in range(len(kpis) - 1):
        k1, k2 = kpis[i], kpis[i + 1]
        s1, s2 = saturations[i], saturations[i + 1]
        if k1 >= kpi_threshold and k2 < kpi_threshold:
            # Interpolation linéaire
            if k1 == k2:
                breaking_point = s2
            else:
                breaking_point = s1 + (s2 - s1) * (
                    (k1 - kpi_threshold) / (k1 - k2)
                )
            break

    # Cas dégénéré : tout est sous le seuil → breaking_point = min saturation
    if breaking_point is None:
        if all(k < kpi_threshold for k in kpis):
            breaking_point = saturations[0]
        # Sinon (tout >= seuil) : breaking_point reste None (robuste partout)

    return RobustesseResult(
        breaking_point_saturation=breaking_point,
        kpi_threshold=kpi_threshold,
        kpi_at_max_saturation=kpis[-1],
        monotone_decreasing=monotone,
    )


@dataclass(frozen=True)
class AgiliteResult:
    """Résultat du calcul d'agilité.

    `recovery_days_per_hazard` : pour chaque hazard, nb de jours
        écoulés entre le hazard et le retour du WIP dans la bande
        ±tolerance autour du pré-hazard. None si aucun retour
        observé sur la fenêtre.
    `mean_recovery_days` : moyenne des récupérations observées
        (None si aucune mesure).
    `n_hazards` : nb total de hazards.
    `n_recoveries_observed` : nb de récupérations effectivement
        détectées.
    """
    recovery_days_per_hazard: list[float | None]
    mean_recovery_days: float | None
    n_hazards: int
    n_recoveries_observed: int


def compute_agilite(
    daily_wip: dict[int, int],
    hazard_days: list[int],
    *,
    tolerance_pct: float = 0.10,
    max_recovery_window_days: int = 10,
    pre_hazard_window: int = 3,
) -> AgiliteResult:
    """Calcule l'agilité depuis daily_wip + hazard_days.

    Pour chaque hazard :
      1. Mesure le WIP nominal pré-hazard = moyenne sur les
         `pre_hazard_window` jours précédant le hazard
      2. Cherche le 1er jour post-hazard (≤ max_recovery_window_days)
         où WIP retombe dans la bande (1 ± tolerance_pct) × nominal
      3. Recovery_days = (jour_retour - jour_hazard). None si pas de
         retour observé dans la fenêtre.

    Note : si le WIP nominal pré-hazard est 0 (système froid), le
    hazard est skip (None) car il n'y a pas de bande à atteindre.

    Edge cases :
      - daily_wip vide → tous None
      - hazard_days vide → résultat vide
      - hazard avant pre_hazard_window jours → utilise les jours
        disponibles
    """
    recoveries: list[float | None] = []
    if not daily_wip or not hazard_days:
        return AgiliteResult(
            recovery_days_per_hazard=[None] * len(hazard_days),
            mean_recovery_days=None,
            n_hazards=len(hazard_days),
            n_recoveries_observed=0,
        )

    for hd in hazard_days:
        # WIP nominal : moyenne sur la fenêtre pré-hazard
        pre_days = [
            d for d in range(max(0, hd - pre_hazard_window), hd)
            if d in daily_wip
        ]
        if not pre_days:
            recoveries.append(None)
            continue
        nominal = sum(daily_wip[d] for d in pre_days) / len(pre_days)
        if nominal == 0:
            recoveries.append(None)
            continue

        # Bande de retour
        low = nominal * (1 - tolerance_pct)
        high = nominal * (1 + tolerance_pct)

        # Cherche le 1er jour post-hazard dans la bande
        recovery = None
        for d in range(hd + 1, hd + 1 + max_recovery_window_days):
            if d not in daily_wip:
                continue
            wip = daily_wip[d]
            if low <= wip <= high:
                recovery = float(d - hd)
                break
        recoveries.append(recovery)

    observed = [r for r in recoveries if r is not None]
    return AgiliteResult(
        recovery_days_per_hazard=recoveries,
        mean_recovery_days=(
            sum(observed) / len(observed) if observed else None
        ),
        n_hazards=len(hazard_days),
        n_recoveries_observed=len(observed),
    )
