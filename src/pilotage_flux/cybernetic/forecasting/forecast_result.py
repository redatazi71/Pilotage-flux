"""Type commun pour les résultats de prévision V12.1."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ForecastResult:
    """Résultat d'un forecast sur N pas futurs.

    Attributes
    ----------
    values : list[float]
        Valeurs prédites pour les N prochains pas.
    lower_ci : list[float] | None
        Borne inférieure de l'intervalle de confiance (None si l'algo
        n'en produit pas).
    upper_ci : list[float] | None
        Borne supérieure de l'intervalle de confiance.
    method_name : str
        Identifiant lisible du forecaster utilisé.
    metadata : dict
        Méta-info libre (paramètres ajustés, AIC, etc.).
    """

    values: list[float]
    lower_ci: list[float] | None
    upper_ci: list[float] | None
    method_name: str
    metadata: dict
