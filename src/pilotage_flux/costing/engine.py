"""Moteur de calcul des coûts (matière, MOD, MOI) — L7.1."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from pilotage_flux.parameters import get_num


DEFAULT_MOI_OVERHEAD_RATE = 0.30      # 30% du MOD
DEFAULT_MOI_FIXED_PER_OF = 50.0       # € fixe par OF (admin, setup, etc.)


@dataclass
class OFCostBreakdown:
    of_id: str
    article_id: str
    quantity: float
    qty_good: float
    qty_scrap: float
    material_cost: float
    mod_cost: float
    moi_cost: float
    scrap_cost: float
    total_cost: float
    cost_per_good_unit: float
    unvalued_articles: list[str] = field(default_factory=list)
    unvalued_workstations: list[str] = field(default_factory=list)


@dataclass
class RunCostReport:
    of_breakdowns: list[OFCostBreakdown] = field(default_factory=list)

    @property
    def total_material(self) -> float:
        return sum(b.material_cost for b in self.of_breakdowns)

    @property
    def total_mod(self) -> float:
        return sum(b.mod_cost for b in self.of_breakdowns)

    @property
    def total_moi(self) -> float:
        return sum(b.moi_cost for b in self.of_breakdowns)

    @property
    def total_scrap(self) -> float:
        return sum(b.scrap_cost for b in self.of_breakdowns)

    @property
    def grand_total(self) -> float:
        return (
            self.total_material + self.total_mod
            + self.total_moi + self.total_scrap
        )

    @property
    def n_ofs(self) -> int:
        return len(self.of_breakdowns)

    @property
    def cost_per_of(self) -> float:
        if self.n_ofs == 0:
            return 0.0
        return self.grand_total / self.n_ofs


def _unit_cost(conn: sqlite3.Connection, article_id: str) -> float | None:
    return get_num(
        conn, scope="article", scope_ref=article_id,
        name="unit_cost", default=None,
    )


def _hourly_rate(conn: sqlite3.Connection, workstation_id: str) -> float | None:
    return get_num(
        conn, scope="workstation", scope_ref=workstation_id,
        name="hourly_rate", default=None,
    )


def _moi_overhead_rate(conn: sqlite3.Connection) -> float:
    val = get_num(
        conn, scope="global", scope_ref=None,
        name="moi_overhead_rate",
        default=DEFAULT_MOI_OVERHEAD_RATE,
    )
    return float(val) if val is not None else DEFAULT_MOI_OVERHEAD_RATE


def _moi_fixed_per_of(conn: sqlite3.Connection) -> float:
    val = get_num(
        conn, scope="global", scope_ref=None,
        name="moi_fixed_per_of",
        default=DEFAULT_MOI_FIXED_PER_OF,
    )
    return float(val) if val is not None else DEFAULT_MOI_FIXED_PER_OF


def _op_duration_minutes(
    actual_start: str | None, actual_end: str | None,
    fallback_unit_time_min: float, quantity: float,
) -> float:
    """Durée effective d'une opération en minutes.

    Si actual_start/end sont présents, utilise leur différence. Sinon
    retombe sur le temps unitaire de la gamme × quantité (estimation).
    """
    if actual_start and actual_end:
        try:
            dt_s = datetime.fromisoformat(actual_start)
            dt_e = datetime.fromisoformat(actual_end)
            return max(0.0, (dt_e - dt_s).total_seconds() / 60.0)
        except ValueError:
            pass
    return fallback_unit_time_min * quantity


def compute_of_cost(
    conn: sqlite3.Connection, of_id: str
) -> OFCostBreakdown:
    """Calcule le breakdown de coût d'un OF.

    Matière = consommations matière réelles (mes_consumptions) × unit_cost.
              Si pas de conso déclarée, on retombe sur BOM × OF.quantity.
    MOD     = pour chaque op réalisée, durée_min × hourly_rate / 60.
    MOI     = moi_overhead_rate × MOD + moi_fixed_per_of.
    Scrap   = qty_scrap × unit_cost(article) — valeur perdue.
    """
    of = conn.execute(
        """
        SELECT of_id, article_id, quantity, qty_good, qty_scrap, status
        FROM manufacturing_orders WHERE of_id = ?
        """,
        (of_id,),
    ).fetchone()
    if of is None:
        raise ValueError(f"OF inconnu : {of_id}")

    article_id = of["article_id"]
    quantity = float(of["quantity"])
    qty_good = float(of["qty_good"] or 0.0)
    qty_scrap = float(of["qty_scrap"] or 0.0)
    unvalued_articles: list[str] = []
    unvalued_ws: list[str] = []

    # --- Matière ---
    material_cost = 0.0
    real_cons = conn.execute(
        """
        SELECT article_id, SUM(qty_consumed) AS qty
        FROM mes_consumptions WHERE of_id = ?
        GROUP BY article_id
        """,
        (of_id,),
    ).fetchall()
    if real_cons:
        for row in real_cons:
            unit = _unit_cost(conn, row["article_id"])
            if unit is None:
                unvalued_articles.append(row["article_id"])
                continue
            material_cost += float(row["qty"]) * float(unit)
    else:
        # Fallback : BOM théorique × OF.quantity
        bom = conn.execute(
            """
            SELECT child_article, quantity FROM bom_lines
            WHERE parent_article = ?
            """,
            (article_id,),
        ).fetchall()
        for row in bom:
            unit = _unit_cost(conn, row["child_article"])
            if unit is None:
                unvalued_articles.append(row["child_article"])
                continue
            material_cost += (
                float(row["quantity"]) * quantity * float(unit)
            )

    # --- MOD : temps réel d'op × taux horaire poste ---
    mod_cost = 0.0
    ops = conn.execute(
        """
        SELECT workstation_id, actual_start, actual_end, status,
               unit_time_min, qty_good
        FROM order_operations WHERE of_id = ?
        """,
        (of_id,),
    ).fetchall()
    for op in ops:
        ws = op["workstation_id"]
        rate = _hourly_rate(conn, ws)
        if rate is None:
            if ws not in unvalued_ws:
                unvalued_ws.append(ws)
            continue
        dur_min = _op_duration_minutes(
            op["actual_start"], op["actual_end"],
            float(op["unit_time_min"]), quantity,
        )
        mod_cost += dur_min * float(rate) / 60.0

    # --- MOI : overhead + fixe par OF ---
    moi_rate = _moi_overhead_rate(conn)
    moi_fixed = _moi_fixed_per_of(conn)
    moi_cost = mod_cost * moi_rate + moi_fixed

    # --- Scrap : valeur perdue ---
    article_unit = _unit_cost(conn, article_id)
    scrap_cost = 0.0
    if article_unit is not None and qty_scrap > 0:
        scrap_cost = qty_scrap * float(article_unit)
    elif article_unit is None and qty_scrap > 0:
        if article_id not in unvalued_articles:
            unvalued_articles.append(article_id)

    total = material_cost + mod_cost + moi_cost + scrap_cost
    cost_per_good = total / qty_good if qty_good > 0 else 0.0

    return OFCostBreakdown(
        of_id=of_id, article_id=article_id, quantity=quantity,
        qty_good=qty_good, qty_scrap=qty_scrap,
        material_cost=round(material_cost, 2),
        mod_cost=round(mod_cost, 2),
        moi_cost=round(moi_cost, 2),
        scrap_cost=round(scrap_cost, 2),
        total_cost=round(total, 2),
        cost_per_good_unit=round(cost_per_good, 2),
        unvalued_articles=unvalued_articles,
        unvalued_workstations=unvalued_ws,
    )


def compute_run_cost_report(
    conn: sqlite3.Connection, *, status_filter: str | None = "closed"
) -> RunCostReport:
    """Agrège les coûts par OF sur tout un run (clôturés par défaut)."""
    sql = "SELECT of_id FROM manufacturing_orders WHERE 1=1"
    params: list[str] = []
    if status_filter:
        sql += " AND status = ?"
        params.append(status_filter)
    sql += " ORDER BY of_id ASC"
    report = RunCostReport()
    for row in conn.execute(sql, params):
        report.of_breakdowns.append(compute_of_cost(conn, row["of_id"]))
    return report


def seed_default_unit_costs(conn: sqlite3.Connection) -> int:
    """Seed des prix unitaires pour les fixtures V1 (ART-A, SEMI-1, COMP-X, COMP-Y)
    et taux horaires pour WS-1/2/3. Idempotent : ne crée que les paramètres
    absents. Renvoie le nombre de paramètres créés.

    Valeurs indicatives (€), pour donner un ordre de grandeur :
      COMP-X = 2 €     COMP-Y = 3 €
      SEMI-1 = 8 €     ART-A  = 18 €
      WS-1   = 35 €/h  WS-2   = 45 €/h  WS-3   = 30 €/h
      moi_overhead_rate = 0.30   moi_fixed_per_of = 50 €
    """
    seeds: list[tuple[str, str | None, str, float]] = [
        ("article", "ART-A", "unit_cost", 18.0),
        ("article", "SEMI-1", "unit_cost", 8.0),
        ("article", "COMP-X", "unit_cost", 2.0),
        ("article", "COMP-Y", "unit_cost", 3.0),
        ("workstation", "WS-1", "hourly_rate", 35.0),
        ("workstation", "WS-2", "hourly_rate", 45.0),
        ("workstation", "WS-3", "hourly_rate", 30.0),
        ("global", None, "moi_overhead_rate", DEFAULT_MOI_OVERHEAD_RATE),
        ("global", None, "moi_fixed_per_of", DEFAULT_MOI_FIXED_PER_OF),
    ]
    n_created = 0
    for scope, ref, name, value in seeds:
        existing = conn.execute(
            """
            SELECT 1 FROM parameters
            WHERE scope = ? AND (scope_ref IS ? OR scope_ref = ?)
              AND name = ? AND valid_to IS NULL
            """,
            (scope, ref, ref, name),
        ).fetchone()
        if existing:
            continue
        conn.execute(
            "INSERT INTO parameters (scope, scope_ref, name, value_num) "
            "VALUES (?, ?, ?, ?)",
            (scope, ref, name, value),
        )
        n_created += 1
    return n_created
