"""Smoke test 6 pilotages × 6 saturations × 1 implantation × 1 seed.

Vérifie que la matrice complète tourne sans crash et collecte
quelques KPIs de premier ordre (OTIF, distribution des
delta_decisions L1..L6 pour les pilotages BCE).

Usage :
    python docs/diagnose_bce_6x6_smoke.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

# Charge le code source local sans installation
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pilotage_flux.comparative.bce_wire import bce_kpis
from pilotage_flux.comparative.runner import run_doctrine
from pilotage_flux.comparative.saturation import (
    SATURATION_TARGETS,
    calibrate_scenario_to_saturation,
)
from pilotage_flux.comparative.scenario import (
    DOCTRINES_6_PILOTAGES,
    baseline_scenario,
)
from pilotage_flux.db import db_session


FIXTURES = Path("data/fixtures_extended")
if not FIXTURES.exists():
    FIXTURES = Path("data/fixtures_v1")
    print(f"[INFO] fallback sur {FIXTURES}")


def _otif(conn) -> float:
    """OTIF = SO non rejetés et livrés à temps / total SO."""
    row = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN status='delivered' OR rejected_at IS NULL THEN 1 ELSE 0 END) AS ok "
        "FROM sales_orders"
    ).fetchone()
    if row is None or row["total"] == 0:
        return 0.0
    return float(row["ok"]) / float(row["total"])


def main() -> int:
    results: list[dict] = []
    n_cells = len(DOCTRINES_6_PILOTAGES) * len(SATURATION_TARGETS)
    print(f"Smoke matrice 6×6 = {n_cells} cellules sur fixtures={FIXTURES}")

    n_crashed = 0
    n_ok = 0
    for sat in SATURATION_TARGETS:
        scenario = baseline_scenario()
        try:
            scenario = calibrate_scenario_to_saturation(
                scenario, sat, fixtures_dir=FIXTURES,
            )
        except Exception as e:
            print(f"[ERREUR] calibrage sat={sat:.2f} : {e}")
            continue

        for doctrine in DOCTRINES_6_PILOTAGES:
            with tempfile.TemporaryDirectory() as td:
                db = Path(td) / f"{doctrine}_sat{sat:.0%}.db"
                try:
                    result = run_doctrine(
                        scenario, doctrine, db,
                        fixtures_dir=FIXTURES,
                        evaluate_rejections=False,
                    )
                    with db_session(db) as conn:
                        otif = _otif(conn)
                        bce_info = (
                            bce_kpis(conn)
                            if doctrine.endswith("_bce")
                            else {"n_decisions_total": 0}
                        )
                    cell = {
                        "doctrine": doctrine,
                        "saturation": sat,
                        "otif": otif,
                        "n_decisions": bce_info["n_decisions_total"],
                        "hazards_observed": len(result.hazards_observed),
                        "status": "ok",
                    }
                    if doctrine.endswith("_bce"):
                        cell["delta_by_niveau"] = bce_info["delta_decisions_by_niveau"]
                        cell["delta_by_cadrage"] = bce_info["delta_decisions_by_cadrage_level"]
                    results.append(cell)
                    n_ok += 1
                except Exception as e:
                    n_crashed += 1
                    results.append({
                        "doctrine": doctrine,
                        "saturation": sat,
                        "status": "crashed",
                        "error": str(e)[:200],
                    })
                    print(f"[CRASH] {doctrine} sat={sat:.0%} : {e}")

    print(f"\n=== Résumé {n_ok} ok / {n_crashed} crash sur {n_cells} cellules ===")
    print()
    for r in results:
        if r["status"] == "crashed":
            print(
                f"  CRASH  {r['doctrine']:<18} sat={r['saturation']:.0%}  "
                f"err: {r['error'][:60]}"
            )
        elif r["doctrine"].endswith("_bce"):
            by = r.get("delta_by_cadrage", {})
            print(
                f"  {r['doctrine']:<18} sat={r['saturation']:.0%}  "
                f"OTIF={r['otif']:.2%}  "
                f"n_decisions={r['n_decisions']}  "
                f"N1={by.get(1, 0)} N2={by.get(2, 0)} "
                f"N3={by.get(3, 0)} N4={by.get(4, 0)}"
            )
        else:
            print(
                f"  {r['doctrine']:<18} sat={r['saturation']:.0%}  "
                f"OTIF={r['otif']:.2%}  "
                f"hazards={r['hazards_observed']}"
            )

    # Sauvegarde JSON pour inspection ultérieure
    out_path = Path("docs/diagnose_bce_6x6_smoke.json")
    out_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nDétail JSON : {out_path}")
    return 0 if n_crashed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
