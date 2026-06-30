"""V12.5 — Matrice d'orchestration : sélection algo selon contexte × profil.

Une `OrchestrationMatrix` lie un `WorkshopProfile` (config statique)
à un `OrchestrationContext` (état runtime) pour produire des
décisions d'algorithmes effectives :

  - quel optimizer utiliser sur la zone négociable (CP-SAT vs heuristique)
  - quel forecaster utiliser sur la zone libre
  - quels seuils L1/L2/L3/L4 appliquer
  - quel délai d'escalation utiliser

Les règles de sélection sont **paramétrables** (chaque seuil dans le
profil) et **explicites** (table de décision documentée).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pilotage_flux.cybernetic.optimization.heuristics import (
    HEURISTIC_ATC, HEURISTIC_SLACK,
)
from pilotage_flux.cybernetic.orchestration.profile import WorkshopProfile


# Identifiants d'optimizers (chaînes pour log et traces audit)
OPTIMIZER_CP_SAT = "cp_sat_dynamic"
OPTIMIZER_HEURISTIC_ATC = f"heuristic_{HEURISTIC_ATC}"
OPTIMIZER_HEURISTIC_SLACK = f"heuristic_{HEURISTIC_SLACK}"

# Identifiants de forecasters
FORECASTER_ENSEMBLE_INV_RMSE = "ensemble_inv_rmse"
FORECASTER_ENSEMBLE_EQUAL = "ensemble_equal"
FORECASTER_BIAS_CORRECTED = "bias_corrected_ensemble"
FORECASTER_HAZARD_AWARE = "hazard_aware_regression"


@dataclass
class OrchestrationContext:
    """État runtime agrégé pour la sélection d'algo."""

    n_of_in_negotiable_zone: int = 0
    n_pending_approvals: int = 0
    n_recent_rejections: int = 0
    historical_bias: float = 0.0
    recent_hazard_count: int = 0
    has_two_bottlenecks: bool = False

    def __post_init__(self) -> None:
        if self.n_of_in_negotiable_zone < 0:
            raise ValueError("n_of_in_negotiable_zone doit être >= 0")
        if self.n_pending_approvals < 0:
            raise ValueError("n_pending_approvals doit être >= 0")


@dataclass
class OrchestrationDecision:
    """Décision produite par la matrice."""

    optimizer: str
    forecaster: str
    autonomy_thresholds: dict[str, float] = field(default_factory=dict)
    overdue_threshold_minutes: float = 240.0
    rationale: list[str] = field(default_factory=list)


class OrchestrationMatrix:
    """Sélectionne les algorithmes en fonction du profil et du contexte."""

    def __init__(self, profile: WorkshopProfile) -> None:
        profile.validate()
        self._profile = profile

    @property
    def profile(self) -> WorkshopProfile:
        return self._profile

    def autonomy_thresholds(self) -> dict[str, float]:
        """Seuils L1/L2/L3/L4 effectifs du profil."""
        return {
            "L1": self._profile.score_threshold_L1,
            "L2": self._profile.score_threshold_L2,
            "L3": self._profile.score_threshold_L3,
            "L4": self._profile.score_threshold_L4,
        }

    def select_optimizer(self, ctx: OrchestrationContext) -> tuple[str, str]:
        """Choisit l'optimizer V12.2 selon le contexte.

        Returns
        -------
        (optimizer_id, rationale) : tuple
        """
        # Règle 1 : si zone négociable trop large pour CP-SAT → heuristique ATC
        if ctx.n_of_in_negotiable_zone > self._profile.cp_sat_max_ofs:
            return (
                OPTIMIZER_HEURISTIC_ATC,
                f"n_ofs={ctx.n_of_in_negotiable_zone} > "
                f"cp_sat_max_ofs={self._profile.cp_sat_max_ofs}, "
                "fallback ATC pour vitesse"
            )
        # Règle 2 : 2 goulots simultanés → DBR limité, heuristique plus robuste
        if ctx.has_two_bottlenecks:
            return (
                OPTIMIZER_HEURISTIC_SLACK,
                "2 goulots simultanés détectés, DBR CP-SAT instable, "
                "fallback SLACK"
            )
        # Défaut : CP-SAT
        return (
            OPTIMIZER_CP_SAT,
            f"n_ofs={ctx.n_of_in_negotiable_zone} <= "
            f"cp_sat_max_ofs={self._profile.cp_sat_max_ofs}, CP-SAT optimal"
        )

    def select_forecaster(self, ctx: OrchestrationContext) -> tuple[str, str]:
        """Choisit le forecaster V12.1 selon le contexte.

        Returns
        -------
        (forecaster_id, rationale) : tuple
        """
        # Règle 1 : biais historique fort → bias-corrected
        if abs(ctx.historical_bias) > 5.0:
            return (
                FORECASTER_BIAS_CORRECTED,
                f"|biais|={abs(ctx.historical_bias):.2f} > 5.0, "
                "bias-corrected ensemble nécessaire"
            )
        # Règle 2 : aléas récents fréquents → hazard-aware
        if ctx.recent_hazard_count >= 5:
            return (
                FORECASTER_HAZARD_AWARE,
                f"recent_hazard_count={ctx.recent_hazard_count} >= 5, "
                "hazard-aware regression conseillée"
            )
        # Règle 3 : rejets récents nombreux → prudence (ensemble inv-RMSE)
        if ctx.n_recent_rejections >= 3:
            return (
                FORECASTER_ENSEMBLE_INV_RMSE,
                f"n_recent_rejections={ctx.n_recent_rejections} >= 3, "
                "ensemble pondéré privilégié"
            )
        # Défaut : ensemble equal
        return (
            FORECASTER_ENSEMBLE_EQUAL,
            "Contexte stable, ensemble equal-weight suffisant"
        )

    def decide(self, ctx: OrchestrationContext) -> OrchestrationDecision:
        """Produit une décision complète avec rationale audit-friendly."""
        opt_id, opt_reason = self.select_optimizer(ctx)
        fc_id, fc_reason = self.select_forecaster(ctx)
        rationale = [
            f"profile={self._profile.name}",
            f"optimizer={opt_id} : {opt_reason}",
            f"forecaster={fc_id} : {fc_reason}",
        ]
        # Ajustement runtime du seuil overdue si beaucoup de pending
        overdue_threshold = self._profile.overdue_threshold_minutes
        if ctx.n_pending_approvals >= 5:
            overdue_threshold = min(overdue_threshold, 120.0)
            rationale.append(
                f"n_pending={ctx.n_pending_approvals} >= 5, "
                f"overdue réduit à {overdue_threshold} min"
            )
        return OrchestrationDecision(
            optimizer=opt_id,
            forecaster=fc_id,
            autonomy_thresholds=self.autonomy_thresholds(),
            overdue_threshold_minutes=overdue_threshold,
            rationale=rationale,
        )
