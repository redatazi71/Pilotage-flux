"""Ext-n — CLI d'export de la trace causale d'un run.

Usage :
    python docs/causal_trace_export.py --db path/to/run.db \
        --out-csv docs/causal_trace.csv --out-md docs/causal_trace_summary.md

Produit également une figure Mermaid embarquable dans le paper §6 pour
illustrer la chaîne « déviation → cause → décision → escalade → outcome ».
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pilotage_flux.comparative.causal_trace import (
    export_causal_trace,
    summarize_trace,
    write_trace_csv,
)


def _mermaid_diagram(rows) -> str:
    """Génère un diagramme Mermaid d'une chaîne causale représentative.

    On sélectionne la première déviation qualifiée `high` ou `critical` avec
    une cause + décision + escalade — c'est le cas d'école pour le paper.
    """
    hard = [
        r for r in rows
        if r.qualification in ("high", "critical")
        and r.cause_rule_id and r.action_level
    ]
    pick = hard[0] if hard else (rows[0] if rows else None)
    if pick is None:
        return "```mermaid\nflowchart LR\n  A[Pas de déviation] --> B[N/A]\n```\n"
    approval_txt = (
        f'{pick.approval_status} · {pick.approval_level}'
        if pick.approval_id else 'auto (L1/L2)'
    )
    return f"""```mermaid
flowchart LR
  D["Déviation #{pick.deviation_id}<br/>{pick.deviation_kind}<br/>Δ={pick.delta_value}"]
  D --> A{{"CPM<br/>{'absorbé' if pick.absorbed_by_cpm else 'non absorbé'}"}}
  A --> C["Cause<br/>{pick.cause_rule_id or 'n/a'}<br/>{pick.cause_label or ''}"]
  C --> S["Décision<br/>{pick.action_level or 'n/a'}<br/>source={pick.decision_source or 'n/a'}"]
  S --> E["Escalade<br/>{approval_txt}"]
```
"""


def _write_summary_md(
    csv_path: Path, rows, summary, out_md: Path
) -> None:
    lines = []
    lines.append(
        "# Trace causale — synthèse\n\n"
        f"Source : `{csv_path}`\n"
        f"Total déviations : **{summary.n_deviations}**\n\n"
        f"- Avec cause identifiée : **{summary.n_with_cause}** "
        f"({summary.n_with_cause / max(1, summary.n_deviations):.1%})\n"
        f"- Avec décision filtre dual : **{summary.n_with_decision}**\n"
        f"- Absorbées par CPM (niveau 0) : **{summary.n_absorbed_cpm}**\n"
        f"- Escaladées en approval_queue : **{summary.n_escalated}**\n"
    )
    if summary.action_level_counts:
        lines.append("\n## Répartition action_level\n")
        for k, v in sorted(
            summary.action_level_counts.items(), key=lambda x: -x[1]
        ):
            lines.append(f"- `{k}` : {v}")
    if summary.cause_counts:
        lines.append("\n\n## Répartition causes racines\n")
        for k, v in sorted(
            summary.cause_counts.items(), key=lambda x: -x[1]
        ):
            lines.append(f"- `{k}` : {v}")
    if summary.source_counts:
        lines.append("\n\n## Source de la décision (V13.C)\n")
        for k, v in sorted(
            summary.source_counts.items(), key=lambda x: -x[1]
        ):
            lines.append(f"- `{k}` : {v}")
    lines.append("\n\n## Diagramme représentatif\n")
    lines.append(_mermaid_diagram(rows))
    out_md.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, required=True,
                   help="Chemin d'un DB de run (SQLite)")
    p.add_argument("--out-csv", type=Path,
                   default=Path("docs/causal_trace.csv"))
    p.add_argument("--out-md", type=Path,
                   default=Path("docs/causal_trace_summary.md"))
    p.add_argument("--out-json", type=Path,
                   default=Path("docs/causal_trace_summary.json"))
    args = p.parse_args()

    if not args.db.exists():
        print(f"[erreur] DB introuvable : {args.db}", file=sys.stderr)
        return 1
    rows = export_causal_trace(args.db)
    summary = summarize_trace(rows)
    write_trace_csv(rows, args.out_csv)
    _write_summary_md(args.db, rows, summary, args.out_md)
    args.out_json.write_text(
        json.dumps({
            "n_deviations": summary.n_deviations,
            "n_with_cause": summary.n_with_cause,
            "n_with_decision": summary.n_with_decision,
            "n_escalated": summary.n_escalated,
            "n_absorbed_cpm": summary.n_absorbed_cpm,
            "action_level_counts": summary.action_level_counts,
            "cause_counts": summary.cause_counts,
            "source_counts": summary.source_counts,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[ok] {args.out_csv} ({len(rows)} déviations)")
    print(f"[ok] {args.out_md}")
    print(f"[ok] {args.out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
