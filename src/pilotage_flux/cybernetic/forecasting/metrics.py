"""Métriques d'évaluation de forecasters V12.1."""

from __future__ import annotations

import math


def _validate(actual: list[float], predicted: list[float]) -> None:
    if len(actual) != len(predicted):
        raise ValueError(
            f"Tailles incompatibles : actual={len(actual)}, "
            f"predicted={len(predicted)}"
        )
    if not actual:
        raise ValueError("Séries vides")


def mae(actual: list[float], predicted: list[float]) -> float:
    """Mean Absolute Error."""
    _validate(actual, predicted)
    return sum(abs(a - p) for a, p in zip(actual, predicted)) / len(actual)


def rmse(actual: list[float], predicted: list[float]) -> float:
    """Root Mean Squared Error."""
    _validate(actual, predicted)
    mse = sum((a - p) ** 2 for a, p in zip(actual, predicted)) / len(actual)
    return math.sqrt(mse)


def mape(actual: list[float], predicted: list[float]) -> float:
    """Mean Absolute Percentage Error en %.

    Les zéros de `actual` sont skipés (division par zéro évitée) ;
    si tous les actuals sont zéro, renvoie 0.
    """
    _validate(actual, predicted)
    pairs = [(a, p) for a, p in zip(actual, predicted) if a != 0]
    if not pairs:
        return 0.0
    return 100.0 * sum(abs((a - p) / a) for a, p in pairs) / len(pairs)
