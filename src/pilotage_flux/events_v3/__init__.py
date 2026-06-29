"""Couche événementielle V3 : attendus vs réels, écarts, qualification."""

from pilotage_flux.events_v3.expected import (
    ExpectedEvent,
    fetch_expected,
    generate_expected_from_batch,
    list_expected,
)
from pilotage_flux.events_v3.matching import (
    Deviation,
    KIND_MISSING,
    KIND_QTY,
    KIND_TIME,
    KIND_UNEXPECTED,
    list_deviations,
    match_actuals_to_expected,
)
from pilotage_flux.events_v3.cpm import (
    CpmAbsorption,
    DEFAULT_MARGIN_MINUTES,
    apply_cpm_absorption,
)

__all__ = [
    "ExpectedEvent",
    "fetch_expected",
    "generate_expected_from_batch",
    "list_expected",
    "Deviation",
    "KIND_MISSING",
    "KIND_QTY",
    "KIND_TIME",
    "KIND_UNEXPECTED",
    "list_deviations",
    "match_actuals_to_expected",
    "CpmAbsorption",
    "DEFAULT_MARGIN_MINUTES",
    "apply_cpm_absorption",
]
