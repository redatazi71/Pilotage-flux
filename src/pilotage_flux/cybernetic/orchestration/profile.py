"""V12.5 — WorkshopProfile : configuration runtime data-driven.

Un profil capture **toutes les politiques V12** d'un atelier :

  - V12.1 forecasting : horizon, holdout, kind d'aléa surveillé
  - V12.2 optimization : freeze window, horizon, seuil CP-SAT/heuristique,
    fragility cap
  - V12.3 delta engine : seuils L1/L2/L3/L4 sur score_combined
  - V12.4 human loop : overdue threshold, auto-approve simulation

Les profils sont **sérialisables JSON** et peuvent être commits dans
un dépôt config (un profil par atelier client).

Trois profils défaut sont fournis :

  - SMALL_PROFILE  : atelier < 20 OF, freeze court (3 j), horizon 14 j
  - MEDIUM_PROFILE : 20–50 OF, freeze 5 j, horizon 28 j (≈ valeurs V11)
  - LARGE_PROFILE  : 50+ OF, freeze 7 j, horizon 42 j, fallback heuristique
                     plus rapide
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class WorkshopProfile:
    """Configuration runtime d'un atelier (sérialisable JSON)."""

    name: str
    description: str = ""

    # V12.2 — Zones temporelles
    freeze_window_days: int = 5
    horizon_forecast_days: int = 28

    # V12.3 — Seuils Delta engine (sur score_combined)
    score_threshold_L1: float = 0.20
    score_threshold_L2: float = 0.40
    score_threshold_L3: float = 0.80
    score_threshold_L4: float = 1.20

    # V12.4 — Workflow humain
    overdue_threshold_minutes: float = 240.0
    auto_approve_l3_in_simulation: bool = False
    auto_approve_l3_mean_lag_min: float = 240.0
    auto_approve_l3_std_lag_min: float = 60.0

    # V12.2.1 — Fragilité postes
    fragility_max_weight: float = 2.0
    fragility_window_days: int = 30

    # V12.1 — Forecasting
    forecast_horizon_days: int = 14
    forecast_holdout_size: int = 10
    forecast_seasonal_period: int = 7

    # V12.2 — Sélection algo
    cp_sat_max_ofs: int = 30
    cp_sat_timeout_sec: float = 10.0

    # Tags libre (catégorie, version, etc.)
    tags: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def validate(self) -> None:
        """Vérifie la cohérence des seuils (L1 < L2 < L3 < L4)."""
        thresholds = [
            self.score_threshold_L1,
            self.score_threshold_L2,
            self.score_threshold_L3,
            self.score_threshold_L4,
        ]
        if thresholds != sorted(thresholds):
            raise ValueError(
                "Les seuils L1/L2/L3/L4 doivent être strictement croissants : "
                f"{thresholds}"
            )
        if self.freeze_window_days < 0:
            raise ValueError("freeze_window_days doit être >= 0")
        if self.horizon_forecast_days <= self.freeze_window_days:
            raise ValueError(
                "horizon_forecast_days doit être > freeze_window_days"
            )
        if self.fragility_max_weight < 1.0:
            raise ValueError("fragility_max_weight doit être >= 1.0")
        if self.cp_sat_timeout_sec <= 0:
            raise ValueError("cp_sat_timeout_sec doit être > 0")


# ---------------------------------------------------------------------
# 3 profils par défaut
# ---------------------------------------------------------------------

SMALL_PROFILE = WorkshopProfile(
    name="small",
    description=(
        "Atelier petit (<20 OF, lead time court). Freeze 3 j, horizon 14 j, "
        "CP-SAT favorisé, escalation rapide 2 h."
    ),
    freeze_window_days=3,
    horizon_forecast_days=14,
    score_threshold_L1=0.15,
    score_threshold_L2=0.35,
    score_threshold_L3=0.70,
    score_threshold_L4=1.00,
    overdue_threshold_minutes=120.0,
    fragility_max_weight=1.8,
    fragility_window_days=14,
    forecast_horizon_days=10,
    forecast_holdout_size=5,
    cp_sat_max_ofs=20,
    cp_sat_timeout_sec=8.0,
    tags={"category": "small"},
)


MEDIUM_PROFILE = WorkshopProfile(
    name="medium",
    description=(
        "Atelier moyen (20–50 OF, valeurs proches de V11 ad hoc). "
        "Freeze 5 j, horizon 28 j, CP-SAT par défaut, escalation 4 h."
    ),
    freeze_window_days=5,
    horizon_forecast_days=28,
    score_threshold_L1=0.20,
    score_threshold_L2=0.40,
    score_threshold_L3=0.80,
    score_threshold_L4=1.20,
    overdue_threshold_minutes=240.0,
    fragility_max_weight=2.0,
    fragility_window_days=30,
    forecast_horizon_days=14,
    forecast_holdout_size=10,
    cp_sat_max_ofs=30,
    cp_sat_timeout_sec=10.0,
    tags={"category": "medium", "is_default": "true"},
)


LARGE_PROFILE = WorkshopProfile(
    name="large",
    description=(
        "Atelier grand (50+ OF, gros volumes). Freeze 7 j, horizon 42 j, "
        "heuristique ATC favorisée au-delà 50 OF, escalation 6 h."
    ),
    freeze_window_days=7,
    horizon_forecast_days=42,
    score_threshold_L1=0.25,
    score_threshold_L2=0.50,
    score_threshold_L3=0.90,
    score_threshold_L4=1.40,
    overdue_threshold_minutes=360.0,
    fragility_max_weight=2.5,
    fragility_window_days=60,
    forecast_horizon_days=21,
    forecast_holdout_size=14,
    cp_sat_max_ofs=50,
    cp_sat_timeout_sec=15.0,
    tags={"category": "large"},
)


DEFAULT_PROFILES = {
    SMALL_PROFILE.name: SMALL_PROFILE,
    MEDIUM_PROFILE.name: MEDIUM_PROFILE,
    LARGE_PROFILE.name: LARGE_PROFILE,
}


# ---------------------------------------------------------------------
# Sérialisation JSON
# ---------------------------------------------------------------------

def save_profile(profile: WorkshopProfile, path: Path) -> None:
    """Sauve un profil au format JSON dans `path`."""
    profile.validate()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(profile.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_profile(path: Path) -> WorkshopProfile:
    """Charge un profil depuis un fichier JSON. Valide la cohérence."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    profile = WorkshopProfile(**data)
    profile.validate()
    return profile
