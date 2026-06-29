"""Rapport comparatif (L4.3) — tableau Markdown + interprétation."""

from __future__ import annotations

from pilotage_flux.comparative.kpis import KpiSet
from pilotage_flux.comparative.scenario import (
    DOCTRINE_EVENT,
    DOCTRINE_FLUX,
    DOCTRINE_OF,
    Scenario,
)


_DOCTRINE_LABEL = {
    DOCTRINE_OF: "APS+MES OF-driven (V0)",
    DOCTRINE_FLUX: "APS+MES flux sans event sourcing (V1+V2)",
    DOCTRINE_EVENT: "APS+MES event sourcing (V3)",
}


def _fmt(value, na: str = "—") -> str:
    if value is None:
        return na
    if isinstance(value, float):
        if value != value:  # NaN
            return na
        return f"{value:.2f}"
    return str(value)


def build_comparative_report(scenario: Scenario, kpis: list[KpiSet]) -> str:
    """Construit le rapport Markdown comparatif des 3 doctrines.

    `kpis` doit contenir les 3 KpiSet (OF, FLUX, EVENT), peu importe l'ordre.
    """
    by_doctrine = {k.doctrine: k for k in kpis}
    ordered = [
        by_doctrine[DOCTRINE_OF],
        by_doctrine[DOCTRINE_FLUX],
        by_doctrine[DOCTRINE_EVENT],
    ]
    lines: list[str] = []
    lines.append(f"# Étude comparative V4 — scénario `{scenario.name}`")
    lines.append("")
    lines.append(
        "Trois doctrines exécutent le même scénario (mêmes commandes, mêmes "
        "aléas, mêmes capacités, même seed) sur trois bases SQLite séparées. "
        "Les KPI ci-dessous mesurent l'apport de chaque palier doctrinal."
    )
    lines.append("")
    lines.append("## Paramètres du scénario")
    lines.append("")
    lines.append(f"- **horizon** : {scenario.horizon_days} jours (départ {scenario.horizon_start})")
    lines.append(f"- **seed** : {scenario.seed}")
    lines.append(f"- **commandes initiales** : {len(scenario.initial_sales_orders)}")
    lines.append(f"- **aléas** : {len(scenario.hazards)}")
    for h in scenario.hazards:
        lines.append(f"  - jour {h.day} : `{h.kind}` — {h.payload}")
    lines.append("")
    lines.append("## Résultats")
    lines.append("")
    header = (
        "| KPI | "
        + " | ".join(_DOCTRINE_LABEL[k.doctrine] for k in ordered)
        + " |"
    )
    sep = "|---|" + "---|" * len(ordered)
    lines.append(header)
    lines.append(sep)

    def row(label: str, key: str, formatter=_fmt) -> str:
        cells = [formatter(getattr(k, key)) for k in ordered]
        return f"| {label} | " + " | ".join(cells) + " |"

    lines.append(row("Lead time moyen (jours)", "lead_time_days_avg"))
    lines.append(row("Lead time max (jours)", "lead_time_days_max"))
    lines.append(row("WIP moyen", "wip_avg"))
    lines.append(row("OF clôturés / créés",
                     "of_closed",
                     lambda v: ""))  # remplacé juste après
    lines[-1] = (
        "| OF clôturés / créés | "
        + " | ".join(f"{k.of_closed}/{k.of_total}" for k in ordered)
        + " |"
    )
    lines.append(row("Recalculs APS", "aps_recalculations"))
    lines.append(row("Nervosité (replan/jour)", "nervousness"))
    lines.append(row("Écarts détectés", "deviations_detected"))
    lines.append(row("Magnitude moyenne écart (min)", "avg_time_deviation_minutes"))
    lines.append(row("Actions tolérance déclenchées", "actions_triggered"))
    lines.append(row("Actions locales (correct_local+replan_local)",
                     "replan_local_actions"))
    lines.append(row("Replans globaux", "replan_global_actions"))
    lines.append(row("Causes attachées", "causes_attached"))
    lines.append(row("Événements qualité", "quality_events"))
    lines.append("")
    lines.append("## Lecture")
    lines.append("")
    of_k = by_doctrine[DOCTRINE_OF]
    flux_k = by_doctrine[DOCTRINE_FLUX]
    event_k = by_doctrine[DOCTRINE_EVENT]
    lines.append(
        f"- **OF-driven** : {of_k.aps_recalculations} recalculs APS pour gérer "
        f"les aléas (replan global systématique). Aucune trace d'écart, aucune "
        f"cause attribuée, pas de qualification proportionnée."
    )
    lines.append(
        f"- **Flux sans event sourcing** : {flux_k.aps_recalculations} recalculs "
        f"APS, qualité tracée ({flux_k.quality_events} événements) mais aucune "
        f"détection événementielle. Les aléas se voient seulement à la clôture."
    )
    lines.append(
        f"- **Event sourcing** : {event_k.deviations_detected} écarts détectés, "
        f"{event_k.actions_triggered} actions filtre dual déclenchées dont "
        f"{event_k.replan_local_actions} corrections locales et "
        f"{event_k.replan_global_actions} replans globaux. {event_k.causes_attached} "
        f"causes attachées. Magnitude moyenne des écarts : "
        f"{_fmt(event_k.avg_time_deviation_minutes)} min."
    )
    lines.append("")
    lines.append("## Hypothèse doctrinale validée ?")
    lines.append("")
    nrv_event_vs_flux = event_k.replan_global_actions <= flux_k.aps_recalculations
    detections_present = event_k.actions_triggered > 0
    causes_present = event_k.causes_attached > 0
    bullets = []
    if nrv_event_vs_flux:
        bullets.append(
            "✓ V3 limite les replans globaux à ce qui est statistiquement "
            "justifié (vs V1+V2 où chaque urgence force un recalcul APS)."
        )
    else:
        bullets.append(
            "⚠ V3 produit autant ou plus de replans globaux que V1+V2 — soit "
            "les seuils du filtre dual sont mal calibrés, soit le scénario "
            "déclenche des écarts massifs."
        )
    if detections_present:
        bullets.append(
            "✓ V3 détecte les écarts en temps réel et produit des décisions "
            "proportionnées (filtre dual de tolérances opérationnel)."
        )
    else:
        bullets.append(
            "⚠ V3 n'a déclenché aucune action filtre dual — vérifier la "
            "génération d'événements attendus et le matching."
        )
    if causes_present:
        bullets.append(
            "✓ Causes racines attachées (le moteur bayésien est opérationnel)."
        )
    else:
        bullets.append(
            "⚠ Aucune cause attachée — la couche causes n'est pas activée."
        )
    lines.extend(f"- {b}" for b in bullets)
    lines.append("")
    return "\n".join(lines)
