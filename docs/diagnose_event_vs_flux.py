"""Pourquoi EVENT ≡ FLUX sur l'OTIF ?

Trace par scénario :
  - Quelles corrective_actions EVENT applique (vs FLUX = aucune)
  - Quels OFs sont stuck dans chaque doctrine
  - Quels OFs sont identiques entre FLUX et EVENT

Hypothèse : EVENT's reactivity touche le coût (faster breakdown recovery,
PO alt-sourcing, qc intervention) mais ne touche PAS la décision de
lancement → smoothing identique → mêmes OFs stuck.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from pilotage_flux.comparative.runner import run_doctrine
from pilotage_flux.comparative.scenario import (
    DOCTRINE_EVENT, DOCTRINE_FLUX,
    baseline_xl_scenario,
    jitter_scenario,
    stress_cascade_nc_xl_scenario,
    stress_demand_spike_xl_scenario,
    stress_double_breakdown_xl_scenario,
)
from pilotage_flux.db import db_session


def trace_doctrine(db_path: Path, label: str, result) -> dict:
    with db_session(db_path) as conn:
        # OFs stuck
        stuck = conn.execute(
            "SELECT of_id, article_id, quantity, qty_good FROM manufacturing_orders "
            "WHERE status = 'in_progress' "
            "AND article_id IN (SELECT article_id FROM sales_orders) "
            "ORDER BY of_id"
        ).fetchall()
        # OFs closed
        closed_n = conn.execute(
            "SELECT COUNT(*) AS n FROM manufacturing_orders WHERE status = 'closed'"
        ).fetchone()["n"]
        # Smoothed offsets (for launch decision comparison)
        offs = conn.execute(
            "SELECT candidate_id, offset_minutes FROM flux_smoothed_launches "
            "ORDER BY candidate_id"
        ).fetchall()
        offsets = {r["candidate_id"]: int(r["offset_minutes"]) for r in offs}

    return {
        "label": label,
        "stuck": [(r["of_id"], r["article_id"], int(r["quantity"]))
                  for r in stuck],
        "closed_n": int(closed_n),
        "n_correctives": len(result.corrective_actions_applied),
        "correctives_summary": [
            f"{c.get('day','?')}:{c.get('effect','?')}"
            for c in result.corrective_actions_applied[:6]
        ],
        "offsets": offsets,
        "aps_replans": result.aps_recalculations,
    }


def main() -> None:
    fixtures = Path("data/fixtures_extended")
    scens = {
        "baseline_xl": baseline_xl_scenario,
        "stress_double_breakdown_xl": stress_double_breakdown_xl_scenario,
        "stress_cascade_nc_xl": stress_cascade_nc_xl_scenario,
        "stress_demand_spike_xl": stress_demand_spike_xl_scenario,
    }
    for sn, fact in scens.items():
        print(f"\n{'=' * 70}\n{sn}\n{'=' * 70}")
        with TemporaryDirectory(prefix="evf_") as tmp:
            work = Path(tmp)
            scen = jitter_scenario(fact(), seed=42)

            db_f = work / "flux.db"
            rf = run_doctrine(scen, DOCTRINE_FLUX, db_f,
                              fixtures_dir=fixtures,
                              evaluate_rejections=False)
            f = trace_doctrine(db_f, "FLUX", rf)

            db_e = work / "event.db"
            re_ = run_doctrine(scen, DOCTRINE_EVENT, db_e,
                               fixtures_dir=fixtures,
                               evaluate_rejections=False)
            e = trace_doctrine(db_e, "EVENT", re_)

        print(f"\n{'Stuck OFs (article=qty)':40} FLUX{'':25} EVENT")
        stuck_f = ", ".join(f"{a}={q}" for _, a, q in f["stuck"]) or "(none)"
        stuck_e = ", ".join(f"{a}={q}" for _, a, q in e["stuck"]) or "(none)"
        print(f"  {'':40}{stuck_f[:28]:28}   {stuck_e[:28]}")
        print(f"  {'OFs closed':40}{f['closed_n']:>4}{'':24}{e['closed_n']:>4}")
        print(f"  {'APS recalcs':40}{f['aps_replans']:>4}{'':24}{e['aps_replans']:>4}")
        print(f"  {'Corrective actions applied':40}{f['n_correctives']:>4}{'':24}{e['n_correctives']:>4}")

        # Diff offsets
        same_offsets = f["offsets"] == e["offsets"]
        print(f"  {'Smoothed offsets identiques ?':40}{str(same_offsets):>34}")
        if not same_offsets:
            for cid in sorted(set(f["offsets"]) | set(e["offsets"])):
                of_v = f["offsets"].get(cid, "MISSING")
                ev_v = e["offsets"].get(cid, "MISSING")
                if of_v != ev_v:
                    print(f"    {cid}: FLUX={of_v} EVENT={ev_v}")

        if e["correctives_summary"]:
            print(f"  EVENT correctives (first 6): {e['correctives_summary']}")


if __name__ == "__main__":
    main()
