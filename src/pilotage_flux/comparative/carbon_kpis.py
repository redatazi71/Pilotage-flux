"""Ext-k — Bilan carbone / énergétique des runs comparatifs.

Modèle simple et défensible, découplé du coût monétaire :

1. **Énergie machine** — chaque opération réalisée consomme
   `kwh_per_hour(ws) × durée_h`, converti en kg CO2 par le facteur
   d'émission local (défaut RTE France 2025 ≈ 55 gCO2eq/kWh).

2. **Surcharge des replans** — chaque événement `replan_global`
   ajoute une pénalité fixe (arrêt/redémarrage postes, changement
   outillage, purge lignes) ; `replan_local` = 30 % de cette pénalité ;
   `correct_local` = 10 %. Valeurs paramétrables par secteur.

3. **Surtransport rupture** — chaque SO non livrée déclenche un
   surtransport express (avion/camion dédié) modélisé comme un multiple
   du fret standard : `co2_penalty_per_rupture_kg`.

4. **WIP capital carbone dormant** — chaque unité en cours porte un
   coût carbone « embodied » (matériaux + transformation partielle) ;
   le KPI `wip_carbon_holding` = wip_avg × embodied_factor × horizon_days,
   proxy du carbone immobilisé sur la fenêtre.

Aucune de ces valeurs n'est de la modélisation LCA rigoureuse — ce sont
des ordres de grandeur qui permettent la **comparaison relative** entre
doctrines. Le message du paper est le classement, pas les niveaux absolus.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import sqlite3


# Facteurs par défaut — source : ADEME base carbone / RTE 2025
EMISSION_FACTOR_KG_PER_KWH_DEFAULT = 0.055
"""France 2025 : 55 gCO2eq/kWh (mix nucléaire dominant)."""

KWH_PER_MACHINE_HOUR_DEFAULT = 12.0
"""Consommation nominale moyenne d'un poste transformation."""

CO2_PENALTY_REPLAN_GLOBAL_KG = 8.0
"""Redémarrages / purge / outillage après un replan_global."""
CO2_PENALTY_REPLAN_LOCAL_KG = 2.4
"""30 % de la pénalité replan_global."""
CO2_PENALTY_CORRECT_LOCAL_KG = 0.8
"""10 % de la pénalité replan_global."""

CO2_PENALTY_PER_RUPTURE_KG = 45.0
"""Surtransport express pour livrer une SO en rupture (multi × fret std)."""

WIP_EMBODIED_KG_PER_UNIT_PER_DAY = 0.12
"""Carbone embodied par unité WIP par jour de séjour dans le flux."""


@dataclass
class CarbonKpiSet:
    """KPI carbone d'un run doctrinal — ordre de grandeur comparatif."""

    doctrine: str
    scenario_name: str

    energy_co2_kg: float
    replan_co2_kg: float
    rupture_co2_kg: float
    wip_holding_co2_kg: float
    co2_total_kg: float

    total_hours_machine: float
    qty_delivered: float
    co2_per_unit: float

    breakdown: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


def _sum_machine_hours(conn: sqlite3.Connection) -> float:
    row = conn.execute("""
        SELECT COALESCE(SUM(
            (JULIANDAY(actual_end) - JULIANDAY(actual_start)) * 24.0
        ), 0.0) AS h
        FROM order_operations
        WHERE actual_end IS NOT NULL AND actual_start IS NOT NULL
    """).fetchone()
    return float(row[0]) if row else 0.0


def _count_actions(conn: sqlite3.Connection) -> tuple[int, int, int]:
    """Retourne (replan_global, replan_local, correct_local)."""
    try:
        row = conn.execute("""
            SELECT
              SUM(CASE WHEN action_level = 'replan_global' THEN 1 ELSE 0 END)
                AS rg,
              SUM(CASE WHEN action_level = 'replan_local' THEN 1 ELSE 0 END)
                AS rl,
              SUM(CASE WHEN action_level = 'correct_local' THEN 1 ELSE 0 END)
                AS cl
            FROM tolerance_filter_decisions
            WHERE triggered_at IS NOT NULL
        """).fetchone()
        if row is None:
            return 0, 0, 0
        return int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)
    except sqlite3.Error:
        return 0, 0, 0


def _count_ruptures(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("""
            SELECT COUNT(*) FROM sales_orders
            WHERE COALESCE(status, '') IN ('rejected', 'unfulfilled')
        """).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0


def _sum_wip_holding(daily_wip: dict[int, float] | None) -> float:
    if not daily_wip:
        return 0.0
    return sum(float(v) for v in daily_wip.values())


def compute_carbon_kpis(
    result,
    db_path: Path,
    *,
    emission_factor: float = EMISSION_FACTOR_KG_PER_KWH_DEFAULT,
    kwh_per_hour: float = KWH_PER_MACHINE_HOUR_DEFAULT,
    co2_replan_global: float = CO2_PENALTY_REPLAN_GLOBAL_KG,
    co2_replan_local: float = CO2_PENALTY_REPLAN_LOCAL_KG,
    co2_correct_local: float = CO2_PENALTY_CORRECT_LOCAL_KG,
    co2_per_rupture: float = CO2_PENALTY_PER_RUPTURE_KG,
    wip_embodied: float = WIP_EMBODIED_KG_PER_UNIT_PER_DAY,
) -> CarbonKpiSet:
    """Calcule le bilan CO2 d'un run à partir de sa DB."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    total_hours = _sum_machine_hours(conn)
    energy_co2 = total_hours * kwh_per_hour * emission_factor

    n_rg, n_rl, n_cl = _count_actions(conn)
    replan_co2 = (
        n_rg * co2_replan_global
        + n_rl * co2_replan_local
        + n_cl * co2_correct_local
    )

    n_ruptures = _count_ruptures(conn)
    rupture_co2 = n_ruptures * co2_per_rupture

    wip_sum = _sum_wip_holding(getattr(result, "daily_wip", None))
    wip_co2 = wip_sum * wip_embodied

    total_co2 = energy_co2 + replan_co2 + rupture_co2 + wip_co2

    qty_row = conn.execute("""
        SELECT COALESCE(SUM(qty_good), 0.0) FROM manufacturing_orders
        WHERE status = 'closed'
    """).fetchone()
    qty_delivered = float(qty_row[0]) if qty_row else 0.0
    conn.close()

    co2_per_unit = total_co2 / qty_delivered if qty_delivered > 0 else 0.0

    return CarbonKpiSet(
        doctrine=result.doctrine,
        scenario_name=result.scenario_name,
        energy_co2_kg=round(energy_co2, 3),
        replan_co2_kg=round(replan_co2, 3),
        rupture_co2_kg=round(rupture_co2, 3),
        wip_holding_co2_kg=round(wip_co2, 3),
        co2_total_kg=round(total_co2, 3),
        total_hours_machine=round(total_hours, 2),
        qty_delivered=round(qty_delivered, 2),
        co2_per_unit=round(co2_per_unit, 4),
        breakdown={
            "n_replan_global": n_rg,
            "n_replan_local": n_rl,
            "n_correct_local": n_cl,
            "n_ruptures": n_ruptures,
            "emission_factor_kg_per_kwh": emission_factor,
            "kwh_per_machine_hour": kwh_per_hour,
        },
    )
