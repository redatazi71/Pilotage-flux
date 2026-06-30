"""Goldilocks — calibrage des scénarios sur les 6 saturations cibles.

Pour chaque (scénario, saturation cible) :
  - calibre le scénario via calibrate_scenario_to_saturation
  - mesure la saturation effective post-calibration
  - rapporte l'écart à la cible

Sortie : tableau scénario × saturation montrant la précision de la
calibration. Tolérance attendue : ±5pp (arrondi à l'unité sur les
quantités SO).
"""

from __future__ import annotations

from pathlib import Path

from pilotage_flux.comparative.saturation import (
    SATURATION_TARGETS,
    _compute_saturation_for_scenario,
    calibrate_scenario_to_saturation,
)
from pilotage_flux.comparative.scenario import (
    baseline_xl_scenario,
    stress_cascade_nc_xl_scenario,
    stress_demand_spike_xl_scenario,
    stress_double_breakdown_xl_scenario,
    stress_multi_contract_overload_scenario,
    baseline_scenario,
)


FIXTURES = Path("data/fixtures_extended")
# 6 scénarios représentatifs (4 XL existants + 2 autres)
SCENARIOS = {
    "baseline_xl":              baseline_xl_scenario,
    "stress_double_breakdown":  stress_double_breakdown_xl_scenario,
    "stress_cascade_nc":        stress_cascade_nc_xl_scenario,
    "stress_demand_spike":      stress_demand_spike_xl_scenario,
    "stress_multi_contract":    stress_multi_contract_overload_scenario,
    "baseline_small":           baseline_scenario,
}


def main() -> None:
    print(f"\nCalibrage saturation R1 — {len(SCENARIOS)} scénarios × "
          f"{len(SATURATION_TARGETS)} cibles\n")
    hdr = f"{'Scénario':28} {'goulot':6} " + " ".join(
        f"{t:>6.2f}" for t in SATURATION_TARGETS
    )
    print(hdr)
    print("-" * len(hdr))

    bad = 0
    for sn, factory in SCENARIOS.items():
        base = factory()
        # Identifier le goulot une fois (sans calibration)
        sat0, ws_goulot = _compute_saturation_for_scenario(base, FIXTURES)
        row = f"{sn:28} {(ws_goulot or '?'):6}"
        for target in SATURATION_TARGETS:
            calibrated = calibrate_scenario_to_saturation(
                base, target, fixtures_dir=FIXTURES,
            )
            measured, _ = _compute_saturation_for_scenario(
                calibrated, FIXTURES,
            )
            delta = measured - target
            tag = " " if abs(delta) < 0.05 else "!"
            row += f" {measured:>5.2f}{tag}"
            if abs(delta) >= 0.05:
                bad += 1
        # Saturation nominale sans calibration
        row += f"   (nat={sat0:.2f})"
        print(row)

    print("-" * len(hdr))
    print(f"\n{bad} cellule(s) hors tolérance ±5pp (marquées !).")
    print("Légende : nat = saturation native du scénario sans calibration.")


if __name__ == "__main__":
    main()
