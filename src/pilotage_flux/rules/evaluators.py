"""Evaluateurs Python des 5 criteres P2 du cadrage (§6, §17).

Chaque evaluateur prend un `RuleContext` et renvoie (outcome, score, explanation).
Les seuils sont lus depuis `parameters` via le module pilotage_flux.parameters
(strict data-driven).
"""

from __future__ import annotations

from datetime import date

from pilotage_flux.aps import get_pegging_chain
from pilotage_flux.parameters import get_num
from pilotage_flux.rules.engine import (
    OUTCOME_BLOCK,
    OUTCOME_PASS,
    OUTCOME_RECALCULATE,
    OUTCOME_RISK,
    RuleContext,
    _register,
)


def _fetch_candidate(ctx: RuleContext) -> dict | None:
    row = ctx.conn.execute(
        """
        SELECT candidate_id, sales_order_id, article_id, quantity, status, zone
        FROM candidate_orders WHERE candidate_id = ?
        """,
        (ctx.candidate_id,),
    ).fetchone()
    return dict(row) if row else None


# -----------------------------------------------------------------------
# R-P2-01 : referentiels presents
# -----------------------------------------------------------------------

def eval_referentials_present(ctx: RuleContext) -> tuple[str, float | None, str]:
    cand = _fetch_candidate(ctx)
    if cand is None:
        return OUTCOME_BLOCK, None, "Candidate inconnu"

    art = ctx.conn.execute(
        "SELECT article_id FROM articles WHERE article_id = ?",
        (cand["article_id"],),
    ).fetchone()
    if art is None:
        return OUTCOME_BLOCK, None, f"Article {cand['article_id']!r} absent du referentiel"

    routing = ctx.conn.execute(
        "SELECT COUNT(*) AS n FROM routing_operations WHERE article_id = ?",
        (cand["article_id"],),
    ).fetchone()
    if int(routing["n"]) == 0:
        return OUTCOME_BLOCK, None, f"Aucune gamme pour {cand['article_id']!r}"

    return OUTCOME_PASS, 1.0, "Referentiels article + gamme presents"


_register("referentials_present", eval_referentials_present)


# -----------------------------------------------------------------------
# R-P2-02 : coherence interne
# -----------------------------------------------------------------------

def eval_internal_coherence(ctx: RuleContext) -> tuple[str, float | None, str]:
    cand = _fetch_candidate(ctx)
    if cand is None:
        return OUTCOME_BLOCK, None, "Candidate inconnu"

    if cand["quantity"] is None or float(cand["quantity"]) <= 0:
        return (
            OUTCOME_BLOCK,
            None,
            f"Quantite invalide : {cand['quantity']!r}",
        )

    invalid_ws = ctx.conn.execute(
        """
        SELECT COUNT(*) AS n FROM routing_operations ro
        LEFT JOIN workstations ws ON ws.workstation_id = ro.workstation_id
        WHERE ro.article_id = ? AND ws.workstation_id IS NULL
        """,
        (cand["article_id"],),
    ).fetchone()
    if int(invalid_ws["n"]) > 0:
        return (
            OUTCOME_BLOCK,
            None,
            "Gamme reference des workstations inconnues",
        )

    return OUTCOME_PASS, 1.0, "Quantite positive et postes coherents"


_register("internal_coherence", eval_internal_coherence)


# -----------------------------------------------------------------------
# R-P2-03 : validite previsionnelle
# -----------------------------------------------------------------------

def eval_forecast_validity(ctx: RuleContext) -> tuple[str, float | None, str]:
    cand = _fetch_candidate(ctx)
    if cand is None:
        return OUTCOME_BLOCK, None, "Candidate inconnu"

    so_id = cand.get("sales_order_id")
    if so_id is None:
        return OUTCOME_PASS, 1.0, "Pas de sales_order rattache (interne)"

    so = ctx.conn.execute(
        "SELECT status, due_date FROM sales_orders WHERE sales_order_id = ?",
        (so_id,),
    ).fetchone()
    if so is None:
        return OUTCOME_BLOCK, None, f"Sales_order {so_id} introuvable"
    if so["status"] != "open":
        return (
            OUTCOME_BLOCK,
            None,
            f"Sales_order {so_id} en statut {so['status']!r}",
        )

    try:
        due = date.fromisoformat(so["due_date"])
    except (TypeError, ValueError):
        return OUTCOME_BLOCK, None, f"Due_date invalide : {so['due_date']!r}"

    today = date.today()
    if due < today:
        return (
            OUTCOME_RECALCULATE,
            0.5,
            f"Due_date {due.isoformat()} depassee (today={today.isoformat()})",
        )
    return OUTCOME_PASS, 1.0, f"SO {so_id} ouvert, due_date {due.isoformat()} >= aujourd'hui"


_register("forecast_validity", eval_forecast_validity)


# -----------------------------------------------------------------------
# R-P2-04 : charge ciblee goulot
# -----------------------------------------------------------------------

def _load_ratio_for_candidate(ctx: RuleContext, candidate_row: dict) -> tuple[str, float]:
    """Calcule le ratio max charge/capacite induit par CE candidate sur ses postes."""
    rows = ctx.conn.execute(
        """
        SELECT ro.workstation_id, ro.unit_time_min,
               (SELECT daily_minutes FROM calendars ORDER BY calendar_id LIMIT 1) AS daily_min,
               (SELECT value_num FROM parameters
                  WHERE scope='workstation' AND scope_ref=ro.workstation_id
                    AND name='capacity_factor' AND valid_to IS NULL
                  ORDER BY version DESC LIMIT 1) AS capa_factor
        FROM routing_operations ro
        WHERE ro.article_id = ?
        """,
        (candidate_row["article_id"],),
    ).fetchall()
    qty = float(candidate_row["quantity"])
    worst_ratio = 0.0
    worst_ws = ""
    for r in rows:
        load = qty * float(r["unit_time_min"])
        capa = float(r["daily_min"] or 0) * float(r["capa_factor"] or 1.0)
        if capa <= 0:
            ratio = float("inf")
        else:
            ratio = load / capa
        if ratio > worst_ratio:
            worst_ratio = ratio
            worst_ws = r["workstation_id"]
    return worst_ws, worst_ratio


def eval_bottleneck_capacity(ctx: RuleContext) -> tuple[str, float | None, str]:
    cand = _fetch_candidate(ctx)
    if cand is None:
        return OUTCOME_BLOCK, None, "Candidate inconnu"

    risk_ratio = get_num(
        ctx.conn,
        scope="global",
        scope_ref=None,
        name="p2_capacity_risk_ratio",
        default=1.0,
    )
    block_ratio = get_num(
        ctx.conn,
        scope="global",
        scope_ref=None,
        name="p2_capacity_block_ratio",
        default=1.5,
    )
    if risk_ratio is None or block_ratio is None:
        return OUTCOME_BLOCK, None, "Seuils capacite absents dans parameters"

    worst_ws, ratio = _load_ratio_for_candidate(ctx, cand)
    if ratio >= float(block_ratio):
        return (
            OUTCOME_BLOCK,
            ratio,
            f"Poste {worst_ws} surcharge >= seuil BLOCK ({ratio:.2f} >= {block_ratio:.2f})",
        )
    if ratio > float(risk_ratio):
        return (
            OUTCOME_RISK,
            ratio,
            f"Poste {worst_ws} surcharge ({ratio:.2f} > seuil RISK {risk_ratio:.2f})",
        )
    return (
        OUTCOME_PASS,
        ratio,
        f"Charge OK sur tous les postes (max {ratio:.2f} sur {worst_ws})",
    )


_register("bottleneck_capacity", eval_bottleneck_capacity)


# -----------------------------------------------------------------------
# R-P2-05 : composants projetables
# -----------------------------------------------------------------------

def eval_components_projectable(ctx: RuleContext) -> tuple[str, float | None, str]:
    """V1.3 : pas de modelisation stocks/achats encore. On verifie juste que les
    composants achetes existent dans le referentiel. Risk implicite tant que
    la modelisation des approvisionnements n'est pas en place (V2)."""
    cand = _fetch_candidate(ctx)
    if cand is None:
        return OUTCOME_BLOCK, None, "Candidate inconnu"

    chain = get_pegging_chain(ctx.conn, "candidate_order", cand["candidate_id"])
    purchased_components = [
        link for link in chain if link.target_type == "component"
    ]
    if not purchased_components:
        return OUTCOME_PASS, 1.0, "Aucun composant achete dans la chaine"

    # Verifier l'existence dans le referentiel articles
    missing = []
    for link in purchased_components:
        row = ctx.conn.execute(
            "SELECT is_purchased FROM articles WHERE article_id = ?",
            (link.article_id,),
        ).fetchone()
        if row is None:
            missing.append(link.article_id)
    if missing:
        return (
            OUTCOME_BLOCK,
            None,
            f"Composant(s) inconnu(s) : {', '.join(missing)}",
        )

    # V1.3 : pas de stock/achats modelises -> on signale un risk leger
    return (
        OUTCOME_RISK,
        0.3,
        f"{len(purchased_components)} composants achetes (stocks/achats non modelises en V1.3)",
    )


_register("components_projectable", eval_components_projectable)
