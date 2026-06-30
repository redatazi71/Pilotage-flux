"""Utilitaires de manipulation de séries temporelles pour V12.1."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TimeSeriesDataset:
    """Encapsule une série temporelle 1-D + ses métadonnées."""

    values: list[float]
    period_label: str = "day"
    name: str = "demand"

    def __len__(self) -> int:
        return len(self.values)

    def head(self, n: int) -> list[float]:
        return self.values[:n]

    def tail(self, n: int) -> list[float]:
        return self.values[-n:]


def split_holdout(
    series: list[float] | TimeSeriesDataset,
    holdout_size: int,
) -> tuple[list[float], list[float]]:
    """Sépare la série en (train, holdout). Le holdout est la queue.

    Raises
    ------
    ValueError
        Si holdout_size <= 0 ou >= len(series).
    """
    if isinstance(series, TimeSeriesDataset):
        values = series.values
    else:
        values = series
    if holdout_size <= 0:
        raise ValueError("holdout_size doit être > 0")
    if holdout_size >= len(values):
        raise ValueError(
            f"holdout_size ({holdout_size}) >= len(series) ({len(values)})"
        )
    return values[:-holdout_size], values[-holdout_size:]
