"""Distribution lissée des lancements d'un contrat de flux.

V1.4 : lissage uniforme proportionnel aux quantités. Chaque candidate reçoit
un `offset_minutes` depuis `horizon_start` pour étaler les démarrages dans
l'horizon. Le takt cible du contrat module l'espacement.

**V12.6 — Due-date aware** : si le paramètre global
`smoothing_due_date_aware` vaut 1, chaque offset est borné par
`latest_start = due_date - duration` du SO parent. Ceci réconcilie le
flux avec l'objectif OTIF (§30) au prix d'un smoothing moins étalé
sur les SOs à due_date courte. Corrige le défaut structurel §24.8.7.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from pilotage_flux.flux.contracts import (
    fetch_contract,
    fetch_version,
    get_candidates_in_version,
)
from pilotage_flux.parameters import get_num, workstation_capacity_factor


@dataclass(frozen=True)
class SmoothedLaunch:
    candidate_id: str
    offset_minutes: int
    planned_start: str


def _horizon_total_minutes(start: str, end: str) -> int:
    try:
        d_start = datetime.fromisoformat(start)
        d_end = datetime.fromisoformat(end)
    except ValueError:
        return 0
    delta = d_end - d_start
    return max(int(delta.total_seconds() // 60), 1)


def _get_due_date_aware_flag(conn: sqlite3.Connection) -> bool:
    """V12.6 — Lit le paramètre `smoothing_due_date_aware` (default 0).

    1 = active la version V12.6 (offsets bornés par latest_start_due)
    0 = version V1.4 historique (smoothing libre sur l'horizon)
    """
    val = get_num(
        conn, scope="global", scope_ref=None,
        name="smoothing_due_date_aware", default=0.0,
    )
    return bool(val and float(val) > 0.5)


def _get_capacity_aware_flag(conn: sqlite3.Connection) -> bool:
    """V13.D — Lit `smoothing_capacity_aware` (default 0).

    1 = active le smoothing capacity-aware : cumul des charges par
        WS × jour, placement earliest-first des candidates au premier
        jour où le cumul de leur charge maintient la saturation
        ≤ `smoothing_target_saturation` (default 0.85). Résout la
        sous-production de FLUX/EVENT identifiée par §28.19.
    0 = version historique (V1.4 linéaire aveugle).
    """
    val = get_num(
        conn, scope="global", scope_ref=None,
        name="smoothing_capacity_aware", default=0.0,
    )
    return bool(val and float(val) > 0.5)


def _get_target_saturation(conn: sqlite3.Connection) -> float:
    """V13.D — Cible de saturation pour le smoothing capacity-aware.

    Default 0.85 (règle industrielle standard : 80-90 % pour éviter
    l'explosion queueing tout en gardant la capacité utile).
    """
    val = get_num(
        conn, scope="global", scope_ref=None,
        name="smoothing_target_saturation", default=0.85,
    )
    f = float(val) if val is not None else 0.85
    return min(0.99, max(0.30, f))


def _get_horizon_aware_flag(conn: sqlite3.Connection) -> bool:
    """V12.7 — Lit le paramètre `smoothing_horizon_aware` (default 0).

    Borne l'offset à `horizon_end - duration × safety_factor` pour
    garantir que l'OF termine avant la fin de simulation. Corrige le
    vrai défaut Q identifié par §28.13 (V12.6 invalidé, V12.7 cible
    la vraie cause).
    """
    val = get_num(
        conn, scope="global", scope_ref=None,
        name="smoothing_horizon_aware", default=0.0,
    )
    return bool(val and float(val) > 0.5)


def _get_horizon_safety_factor(conn: sqlite3.Connection) -> float:
    """V12.7 — Lit `smoothing_horizon_safety_factor` (default 10.0).

    Multiplicateur appliqué à la durée estimée pour absorber
    l'attente sur workstations (queueing) que la simple somme
    unit_time × quantity n'inclut pas. Doit être > 1.
    """
    val = get_num(
        conn, scope="global", scope_ref=None,
        name="smoothing_horizon_safety_factor", default=10.0,
    )
    f = float(val) if val is not None else 10.0
    return max(1.0, f)


def _estimate_candidate_duration_min(
    conn: sqlite3.Connection, candidate_id: str
) -> int:
    """Estime la durée totale du candidate.

    durée = quantity × Σ(unit_time_min des operations du routing).
    Fallback : 960 min. Plancher : 60 min.

    Note : ne modélise pas l'attente sur les workstations (queueing).
    Un multiplicateur de sécurité est ajouté pour les contextes
    multi-OFs (voir `compute_smoothing`).
    """
    row = conn.execute(
        """
        SELECT
            COALESCE(c.quantity, 1) AS qty,
            COALESCE(SUM(r.unit_time_min), 0) AS unit_total
        FROM candidate_orders c
        LEFT JOIN routing_operations r ON r.article_id = c.article_id
        WHERE c.candidate_id = ?
        GROUP BY c.candidate_id
        """,
        (candidate_id,),
    ).fetchone()
    if row is None:
        return 960
    qty = float(row["qty"] or 1.0)
    unit_total = float(row["unit_total"] or 0.0)
    duration_min = int(round(qty * unit_total)) if unit_total > 0 else 960
    if duration_min < 60:
        duration_min = 60
    return duration_min


def _compute_latest_start_minutes(
    conn: sqlite3.Connection,
    candidate_id: str,
    horizon_start: str,
    fallback_min: int,
) -> int:
    """V12.6 — Calcule le `latest_start_minutes` d'un candidat.

    `latest_start = (due_date - duration_estimée) − horizon_start`
    en minutes. Si la candidate n'a pas de SO parent ou pas de due_date,
    on renvoie `fallback_min` (= horizon total → smoothing libre).
    """
    row = conn.execute(
        """
        SELECT so.due_date
        FROM candidate_orders c
        JOIN sales_orders so ON so.sales_order_id = c.sales_order_id
        WHERE c.candidate_id = ?
        """,
        (candidate_id,),
    ).fetchone()
    if row is None or not row["due_date"]:
        return fallback_min

    try:
        due_dt = datetime.fromisoformat(row["due_date"])
        start_dt = datetime.fromisoformat(horizon_start)
    except (ValueError, TypeError):
        return fallback_min

    duration_min = _estimate_candidate_duration_min(conn, candidate_id)
    latest_start_min = int(
        (due_dt - start_dt).total_seconds() // 60
    ) - duration_min
    return max(0, latest_start_min)


def _get_cpm_aware_flag(conn: sqlite3.Connection) -> bool:
    """V12.8 — Lit `smoothing_cpm_aware` (default 0).

    Active la borne CPM data-driven : makespan par candidate calculé
    via routing_operations × queueing_factor par WS (Little's law).
    Remplace le `safety_factor` magique de V12.7 par une mesure du
    taux d'utilisation effectif de chaque workstation.
    """
    val = get_num(
        conn, scope="global", scope_ref=None,
        name="smoothing_cpm_aware", default=0.0,
    )
    return bool(val and float(val) > 0.5)


def _get_slack_ordering_flag(conn: sqlite3.Connection) -> bool:
    """V12.8 — Lit `smoothing_slack_ordering` (default 0).

    Réordonne les candidats par slack croissant (latest_start_cpm
    ascendant) avant d'attribuer les offsets linéaires. Aligne le
    smoothing sur la doctrine SLACK V12.2.3 (heuristique classique
    de séquencement : least slack first).
    """
    val = get_num(
        conn, scope="global", scope_ref=None,
        name="smoothing_slack_ordering", default=0.0,
    )
    return bool(val and float(val) > 0.5)


def _get_bom_topo_flag(conn: sqlite3.Connection) -> bool:
    """V12.8 — Lit `smoothing_bom_topo` (default 0).

    Réordonne les candidats par profondeur BOM ascendante (les
    composants avant les articles finis). Indispensable car le
    smoothing linéaire historique met les parents en premier
    (offset=0) alors que leurs composants n'ont pas encore été
    produits.
    """
    val = get_num(
        conn, scope="global", scope_ref=None,
        name="smoothing_bom_topo", default=0.0,
    )
    return bool(val and float(val) > 0.5)


def _compute_article_bom_level(conn: sqlite3.Connection) -> dict[str, int]:
    """V12.8 — Profondeur BOM par article (longueur du chemin le plus
    long depuis l'article jusqu'à une feuille achetée).

    - Article fini sans composants à produire (feuille du graphe
      produits) : level = 0
    - Article ayant des composants : level = 1 + max(level(child))

    Renvoie un dict article_id → level (≥ 0).
    """
    rows = conn.execute(
        "SELECT parent_article, child_article FROM bom_lines"
    ).fetchall()
    children: dict[str, list[str]] = {}
    for r in rows:
        children.setdefault(r["parent_article"], []).append(
            r["child_article"]
        )
    level: dict[str, int] = {}

    def _level(article: str) -> int:
        if article in level:
            return level[article]
        kids = children.get(article, [])
        if not kids:
            level[article] = 0
            return 0
        level[article] = 1 + max(_level(c) for c in kids)
        return level[article]

    # Cover all articles that appear in bom_lines
    seen = set()
    for r in rows:
        seen.add(r["parent_article"])
        seen.add(r["child_article"])
    for a in seen:
        _level(a)
    return level


def _get_queueing_rho_cap(conn: sqlite3.Connection) -> float:
    """V12.8 — Plafond de saturation `smoothing_queueing_rho_cap`
    (default 0.95).

    Borne supérieure de l'utilisation ρ par workstation dans le
    calcul du facteur d'attente `1 / (1 - ρ)`. ρ → 1 ⇒ queue → ∞ ;
    on plafonne pour rester numériquement stable.
    """
    val = get_num(
        conn, scope="global", scope_ref=None,
        name="smoothing_queueing_rho_cap", default=0.95,
    )
    f = float(val) if val is not None else 0.95
    return min(0.99, max(0.5, f))


def _compute_workstation_queueing_factors(
    conn: sqlite3.Connection,
    horizon_min: int,
    rho_cap: float = 0.95,
) -> dict[str, float]:
    """V12.8 — Facteur d'attente par workstation, combinant Little (charge
    moyenne) et concurrence (charge crête bursty).

    Pour chaque WS :
      n_competitors = nombre de candidates qui routent par ce WS
      ρ_mean        = total_processing / horizon_capacity (Little)
      factor_little = 1 / max(1 - min(ρ, ρ_cap), 1 - ρ_cap)
      factor        = max(n_competitors, factor_little)

    Le `max` capture le pire des deux : steady-state Little
    sous-estime la concurrence bursty au démarrage du smoothing
    (plusieurs OFs lancés simultanément sur le même WS), donc on
    prend le `n_competitors` comme borne inférieure stricte du
    facteur d'attente effectif.
    """
    rows = conn.execute(
        """
        SELECT
            r.workstation_id AS ws,
            SUM(r.unit_time_min * c.quantity) AS load_raw,
            COUNT(DISTINCT c.candidate_id) AS n_competitors
        FROM candidate_orders c
        JOIN routing_operations r ON r.article_id = c.article_id
        WHERE c.status IN ('candidate', 'promoted')
        GROUP BY r.workstation_id
        """
    ).fetchall()
    factors: dict[str, float] = {}
    for r in rows:
        ws = r["ws"]
        load_raw = float(r["load_raw"] or 0.0)
        n_comp = int(r["n_competitors"] or 1)
        capa = workstation_capacity_factor(conn, ws)
        load_eff = load_raw / max(capa, 0.01)
        rho = min(load_eff / max(horizon_min, 1.0), rho_cap)
        factor_little = 1.0 / max(1.0 - rho, 1.0 - rho_cap)
        factors[ws] = max(float(n_comp), factor_little)
    return factors


def _estimate_candidate_makespan_cpm(
    conn: sqlite3.Connection,
    candidate_id: str,
    ws_factors: dict[str, float],
) -> int:
    """V12.8 — Makespan CPM par candidate avec queueing par WS.

    makespan = Σ_op ( unit_time × qty / capa_ws × queueing_factor_ws )

    Modélise un routing linéaire séquentiel (chaque op succède à
    la précédente). Les facteurs queueing reflètent la charge
    globale du WS — c'est le critical_path en présence de
    contention de ressources.
    """
    rows = conn.execute(
        """
        SELECT
            r.workstation_id AS ws,
            r.unit_time_min AS unit_time,
            c.quantity AS qty
        FROM candidate_orders c
        JOIN routing_operations r ON r.article_id = c.article_id
        WHERE c.candidate_id = ?
        ORDER BY r.sequence_idx
        """,
        (candidate_id,),
    ).fetchall()
    if not rows:
        return 960
    total_min = 0.0
    for r in rows:
        ws = r["ws"]
        capa = workstation_capacity_factor(conn, ws)
        factor = ws_factors.get(ws, 1.0)
        op_dur = (
            float(r["unit_time"]) * float(r["qty"])
            / max(capa, 0.01)
            * factor
        )
        total_min += op_dur
    return max(60, int(round(total_min)))


def _compute_latest_start_cpm_minutes(
    conn: sqlite3.Connection,
    candidate_id: str,
    horizon_total_min: int,
    ws_factors: dict[str, float],
) -> int:
    """V12.8 — Cap CPM-aware sur l'offset.

    `latest_start_cpm = horizon_total_min - makespan_cpm`.
    """
    makespan = _estimate_candidate_makespan_cpm(
        conn, candidate_id, ws_factors,
    )
    return max(0, horizon_total_min - makespan)


def _compute_latest_start_horizon_minutes(
    conn: sqlite3.Connection,
    candidate_id: str,
    horizon_total_min: int,
    safety_factor: float = 10.0,
) -> int:
    """V12.7 — Calcule l'offset maximal tel que l'OF puisse terminer
    avant `horizon_end`, incluant un facteur d'attente queueing.

    `latest_start_horizon = horizon_total_min - duration × safety_factor`.

    Le multiplicateur `safety_factor` couvre l'écart entre temps de
    cycle Σ(unit_time × qty) et temps elapsed réel (queueing,
    setups, transferts inter-WS). Valeur typique : 10-50× selon le
    taux d'utilisation des workstations.
    """
    duration_min = _estimate_candidate_duration_min(conn, candidate_id)
    effective_duration = int(round(duration_min * max(1.0, safety_factor)))
    return max(0, horizon_total_min - effective_duration)


def _candidate_ws_loads(
    conn: sqlite3.Connection, article_id: str, qty: float,
) -> list[tuple[str, float]]:
    """Retourne [(ws, minutes_de_travail_machine), ...] pour ce candidate,
    dans l'ordre du routing. Charge = qty × unit_time (temps machine
    brut, sans division par capa). La capa est appliquée côté budget
    (capacité disponible par jour), pas côté charge."""
    rows = conn.execute(
        """SELECT workstation_id, unit_time_min
           FROM routing_operations
           WHERE article_id = ?
           ORDER BY sequence_idx ASC""",
        (article_id,),
    ).fetchall()
    out: list[tuple[str, float]] = []
    for r in rows:
        ws = r["workstation_id"]
        load = float(r["unit_time_min"]) * qty
        out.append((ws, load))
    return out


def _daily_minutes(conn: sqlite3.Connection) -> int:
    """Lit le calendrier par défaut ; fallback 480 (1×8h) si absent."""
    row = conn.execute(
        "SELECT daily_minutes FROM calendars LIMIT 1"
    ).fetchone()
    if row and row["daily_minutes"]:
        return int(row["daily_minutes"])
    return 480


def _candidate_due_offset_min(
    conn: sqlite3.Connection, candidate_id: str, horizon_start: str,
    fallback_min: int,
) -> int:
    """Offset en minutes de la due_date du SO parent depuis horizon_start.
    Sert à trier les candidates par urgence (EDD) avant placement."""
    row = conn.execute(
        """SELECT so.due_date FROM sales_orders so
           JOIN candidate_orders c ON c.sales_order_id = so.sales_order_id
           WHERE c.candidate_id = ?""",
        (candidate_id,),
    ).fetchone()
    if not row or not row["due_date"]:
        return fallback_min
    try:
        due_dt = datetime.fromisoformat(row["due_date"])
        start_dt = datetime.fromisoformat(horizon_start)
        return int((due_dt - start_dt).total_seconds() // 60)
    except ValueError:
        return fallback_min


def _compute_capacity_aware_offsets(
    conn: sqlite3.Connection,
    candidates: list,
    horizon_min: int,
    target_saturation: float,
    horizon_start: str | None = None,
) -> dict[str, int]:
    """V13.D — Cumul charges + capacités par WS×jour, earliest-first.

    Algorithme :
      1. Trie candidates par due_date ascendante (EDD) — les plus
         urgents d'abord.
      2. Capacité par WS × jour = daily_min × capa_factor
      3. Budget par WS × jour   = capacité × target_saturation
      4. Pour chaque candidate :
           a. Extrait sa charge par WS (Σ des ops)
           b. Trouve le jour le plus tôt où le cumul WS×jour +
              charge ≤ budget pour tous les WS impactés
           c. Enregistre offset = jour × 1440
           d. Ajoute la charge au cumul du jour choisi
      5. Si aucun jour ne convient : pose au dernier jour (best effort).
    """
    horizon_days = max(1, horizon_min // 1440)
    daily_min = _daily_minutes(conn)

    # EDD : trie par due_date ascendante (candidates urgents en premier).
    # Sans horizon_start on ne peut pas trier, on garde l'ordre reçu.
    if horizon_start:
        candidates = sorted(
            candidates,
            key=lambda c: _candidate_due_offset_min(
                conn, c["candidate_id"], horizon_start, horizon_min,
            ),
        )

    ws_rows = conn.execute(
        "SELECT workstation_id FROM workstations"
    ).fetchall()
    ws_capacity: dict[str, float] = {}
    for wsr in ws_rows:
        ws = wsr["workstation_id"]
        capa = workstation_capacity_factor(conn, ws)
        ws_capacity[ws] = daily_min * capa

    # ws_daily_load[ws][day] = minutes de charge déjà cumulées
    ws_daily_load: dict[str, list[float]] = {
        ws: [0.0] * horizon_days for ws in ws_capacity
    }

    offsets: dict[str, int] = {}
    for cand in candidates:
        cid = cand["candidate_id"]
        qty = float(cand["qty_in_contract"])
        loads = _candidate_ws_loads(conn, cand["article_id"], qty)
        if not loads:
            offsets[cid] = 0
            continue

        # Trouve le jour le plus tôt où placer le candidate (toutes
        # ses ops le même jour d'assignation) respecte le budget
        # sur chaque WS impacté. Sépare les charges par WS (l'op1 et
        # l'op2 sur des WS différents ne se cumulent pas).
        loads_by_ws: dict[str, float] = {}
        for ws, load in loads:
            loads_by_ws[ws] = loads_by_ws.get(ws, 0.0) + load

        earliest = -1
        for day in range(horizon_days):
            fits = True
            for ws, total_load in loads_by_ws.items():
                budget = ws_capacity.get(ws, daily_min) * target_saturation
                if ws_daily_load[ws][day] + total_load > budget:
                    fits = False
                    break
            if fits:
                earliest = day
                break

        if earliest < 0:
            # Aucun jour ne convient à saturation cible — dégradation
            # gracieuse : choix du jour minimisant le surplus (au plus
            # tôt en cas d'égalité). Évite le fallback abrupt vers la
            # fin d'horizon qui provoque des rejets massifs de SO.
            best_day = 0
            best_excess = float("inf")
            for day in range(horizon_days):
                excess = 0.0
                for ws, total_load in loads_by_ws.items():
                    budget = (
                        ws_capacity.get(ws, daily_min) * target_saturation
                    )
                    excess += max(
                        0.0, ws_daily_load[ws][day] + total_load - budget
                    )
                if excess < best_excess:
                    best_excess = excess
                    best_day = day
            earliest = best_day

        offsets[cid] = earliest * 1440
        for ws, total_load in loads_by_ws.items():
            ws_daily_load[ws][earliest] += total_load

    return offsets


def compute_smoothing(
    conn: sqlite3.Connection, contract_id: str, version: int | None = None
) -> list[SmoothedLaunch]:
    """Calcule la distribution lissée et la persiste dans flux_smoothed_launches.

    Algorithme V1.4 (simple, déterministe) : on étale les démarrages sur
    l'horizon total, espacés proportionnellement aux quantités cumulées.
    L'offset_minutes du i-ème candidate = (sum_qty[0..i] / total) × horizon.

    V12.6 (data-driven, via `smoothing_due_date_aware = 1`) : chaque
    offset est borné par `latest_start = due_date - duration` afin de
    garantir que la livraison reste possible avant la due_date.

    V12.7 (via `smoothing_horizon_aware = 1`) : chaque offset est borné
    par `horizon_end - duration` pour garantir que l'OF termine avant
    la fin de simulation. Cible la vraie cause du défaut Q (OFs stuck
    `in_progress` avec qty_good=0) identifiée par §28.13 après que
    V12.6 a été invalidé.

    V12.8 (via `smoothing_cpm_aware = 1`) : remplace le multiplicateur
    constant V12.7 par un facteur de queueing par workstation calculé
    via Little's law (`1 / (1 − ρ_ws)`). Le makespan CPM par candidate
    intègre la charge globale du WS. Si `smoothing_slack_ordering = 1`,
    les candidats sont en plus réordonnés par slack ascendant
    (heuristique SLACK V12.2.3 : least slack first). Cf. §28.14.
    """
    contract = fetch_contract(conn, contract_id)
    if contract is None:
        raise ValueError(f"Contrat inconnu : {contract_id}")
    if version is None:
        version = contract.current_version
    ver = fetch_version(conn, contract_id, version)
    if ver is None:
        raise ValueError(f"Version {version} inconnue pour {contract_id}")

    candidates = get_candidates_in_version(conn, contract_id, version)
    if not candidates:
        return []

    total_qty = float(ver.total_quantity)
    horizon_min = _horizon_total_minutes(
        contract.horizon_start, contract.horizon_end
    )
    if total_qty <= 0 or horizon_min <= 0:
        return []

    start_dt = datetime.fromisoformat(contract.horizon_start)
    due_date_aware = _get_due_date_aware_flag(conn)
    horizon_aware = _get_horizon_aware_flag(conn)
    safety_factor = (
        _get_horizon_safety_factor(conn) if horizon_aware else 1.0
    )
    cpm_aware = _get_cpm_aware_flag(conn)
    slack_ordering = _get_slack_ordering_flag(conn)
    bom_topo = _get_bom_topo_flag(conn)
    capacity_aware = _get_capacity_aware_flag(conn)
    target_saturation = _get_target_saturation(conn) if capacity_aware else 0.85
    rho_cap = _get_queueing_rho_cap(conn)
    ws_factors: dict[str, float] = (
        _compute_workstation_queueing_factors(conn, horizon_min, rho_cap)
        if cpm_aware else {}
    )

    # V12.8 BOM topological ordering : composants AVANT produits finis.
    # Sans ça, le smoothing linéaire met le parent à offset=0 alors que
    # ses composants ne seront prêts qu'à offset > 0. Le tri par BOM
    # level ascendant (feuilles d'abord) corrige cette inversion.
    if bom_topo:
        levels = _compute_article_bom_level(conn)
        candidates = sorted(
            candidates,
            key=lambda c: (
                levels.get(c["article_id"], 0),
                -float(c.get("qty_in_contract") or 0.0),
            ),
        )

    # V12.8 SLACK ordering : trie par latest_start_cpm croissant
    # (least slack first). Appliqué APRÈS le tri BOM si les deux flags
    # sont actifs ; en pratique on choisit l'un OU l'autre.
    if cpm_aware and slack_ordering:
        candidates = sorted(
            candidates,
            key=lambda c: _compute_latest_start_cpm_minutes(
                conn, c["candidate_id"], horizon_min, ws_factors,
            ),
        )

    # V13.D — Si smoothing_capacity_aware, on remplace le calcul
    # linéaire par un placement earliest-first à saturation cible.
    capacity_offsets: dict[str, int] = (
        _compute_capacity_aware_offsets(
            conn, candidates, horizon_min, target_saturation,
            horizon_start=contract.horizon_start,
        ) if capacity_aware else {}
    )

    # Calcul cumulatif : le i-ème candidate démarre quand on a déjà engagé
    # somme(qty[0..i-1]) sur le total.
    conn.execute(
        "DELETE FROM flux_smoothed_launches WHERE contract_id = ? AND version = ?",
        (contract_id, version),
    )
    out: list[SmoothedLaunch] = []
    running = 0.0
    for cand in candidates:
        qty = float(cand["qty_in_contract"])
        if capacity_aware:
            offset_min = capacity_offsets.get(cand["candidate_id"], 0)
        else:
            offset_min = int(round((running / total_qty) * horizon_min))
        if due_date_aware:
            latest_start_due = _compute_latest_start_minutes(
                conn,
                candidate_id=cand["candidate_id"],
                horizon_start=contract.horizon_start,
                fallback_min=horizon_min,
            )
            offset_min = min(offset_min, latest_start_due)
        if horizon_aware:
            # V12.7 : borne par horizon_end - duration × safety pour
            # que l'OF ait le temps de terminer avant la fin de
            # simulation (incluant l'attente queueing). Corrige la
            # vraie cause du défaut Q identifiée par §28.13.
            latest_start_horizon = _compute_latest_start_horizon_minutes(
                conn,
                candidate_id=cand["candidate_id"],
                horizon_total_min=horizon_min,
                safety_factor=safety_factor,
            )
            offset_min = min(offset_min, latest_start_horizon)
        if cpm_aware:
            # V12.8 : borne CPM data-driven via Little's law par WS.
            latest_start_cpm = _compute_latest_start_cpm_minutes(
                conn,
                candidate_id=cand["candidate_id"],
                horizon_total_min=horizon_min,
                ws_factors=ws_factors,
            )
            offset_min = min(offset_min, latest_start_cpm)
        planned_dt = start_dt + timedelta(minutes=offset_min)
        planned_start_iso = planned_dt.isoformat(sep=" ")
        conn.execute(
            """
            INSERT INTO flux_smoothed_launches
                (contract_id, version, candidate_id, offset_minutes, planned_start)
            VALUES (?, ?, ?, ?, ?)
            """,
            (contract_id, version, cand["candidate_id"], offset_min, planned_start_iso),
        )
        out.append(
            SmoothedLaunch(
                candidate_id=cand["candidate_id"],
                offset_minutes=offset_min,
                planned_start=planned_start_iso,
            )
        )
        running += qty

    return out


def get_smoothed_launches(
    conn: sqlite3.Connection, contract_id: str, version: int | None = None
) -> list[SmoothedLaunch]:
    if version is None:
        contract = fetch_contract(conn, contract_id)
        if contract is None:
            return []
        version = contract.current_version
    rows = conn.execute(
        """
        SELECT candidate_id, offset_minutes, planned_start
        FROM flux_smoothed_launches
        WHERE contract_id = ? AND version = ?
        ORDER BY offset_minutes ASC
        """,
        (contract_id, version),
    ).fetchall()
    return [
        SmoothedLaunch(
            candidate_id=r["candidate_id"],
            offset_minutes=int(r["offset_minutes"]),
            planned_start=r["planned_start"],
        )
        for r in rows
    ]
