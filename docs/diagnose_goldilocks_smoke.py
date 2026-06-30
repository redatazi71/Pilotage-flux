"""Goldilocks — smoke test de la matrice (réduit).

Vérifie que la combinatoire saturation × implantation × pilotage tourne
de bout en bout sans crash, avec mesure OTIF/coût pour confirmer que
les valeurs varient bien selon les axes.

Matrice réduite : 1 scénario × 6 saturations × 3 implantations × 2
politiques × 1 seed = 36 runs (~3-5 min).
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from pilotage_flux.comparative.kpis import compute_kpis
from pilotage_flux.comparative.runner import run_doctrine
from pilotage_flux.comparative.saturation import (
    ROUTING_STRATEGIES,
    ROUTING_STRATEGY_CODE,
    SATURATION_TARGETS,
    calibrate_scenario_to_saturation,
)
from pilotage_flux.comparative.scenario import (
    DOCTRINE_FLUX, DOCTRINE_OF,
    baseline_xl_scenario, jitter_scenario,
)


FIXTURES = Path("data/fixtures_extended")
POLICIES = [("OF", DOCTRINE_OF), ("FLUX", DOCTRINE_FLUX)]


def main() -> None:
    print(f"\nSmoke test : 1 scen × {len(SATURATION_TARGETS)} sat × "
          f"{len(ROUTING_STRATEGIES)} impl × {len(POLICIES)} pol × 1 seed = "
          f"{len(SATURATION_TARGETS) * len(ROUTING_STRATEGIES) * len(POLICIES)} runs\n")

    base = baseline_xl_scenario()
    seed = 42

    hdr = f"{'pol':6} {'impl':10} " + " ".join(
        f"{s:>5.2f}" for s in SATURATION_TARGETS
    )
    print(hdr)
    print("-" * len(hdr))

    crashes = 0
    for pol_name, doc in POLICIES:
        for impl in ROUTING_STRATEGIES:
            row = f"{pol_name:6} {impl:10}"
            for sat in SATURATION_TARGETS:
                cal = calibrate_scenario_to_saturation(
                    base, sat, fixtures_dir=FIXTURES,
                )
                scen = jitter_scenario(cal, seed=seed)
                try:
                    with TemporaryDirectory(prefix="gold_") as tmp:
                        db = Path(tmp) / "x.db"
                        r = run_doctrine(
                            scen, doc, db, fixtures_dir=FIXTURES,
                            evaluate_rejections=True,
                            late_threshold_days=3,
                            param_overrides={
                                ("global", None, "routing_strategy_code"):
                                    float(ROUTING_STRATEGY_CODE[impl]),
                            },
                        )
                        k = compute_kpis(scen, r)
                        otif = k.quantity_compliance * k.disponibility_so_level
                        row += f" {otif:>5.2f}"
                except Exception as e:  # noqa: BLE001
                    crashes += 1
                    row += f" {'CRASH':>5}"
                    print(f"\n  ERREUR ({pol_name}/{impl}/sat={sat}): {e}\n")
                    break
            print(row)

    print("-" * len(hdr))
    print(f"\n{crashes} crash(es). Cellules : OTIF mesuré (Q×D).")


if __name__ == "__main__":
    main()
