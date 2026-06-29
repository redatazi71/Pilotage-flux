"""Cohérence collective P3 multi-contrats (L6.1 / cadrage §180.g).

Le `run_p3_freeze` historique gèle un contrat à la fois. En production,
plusieurs contrats négocient le même horizon et partagent les goulots —
il faut donc évaluer leur cohérence **collective** : la somme des charges
sur le poste contraint ne doit pas dépasser sa capacité d'horizon, sinon
on tombe dans le surengagement transparent.

Décisions possibles :
  - FREEZE_ALL    : tous les contrats passent ; ils sont gelés ensemble
  - PARTIAL_FREEZE: certains contrats sont gelés, d'autres reportés (par
                    ordre de priorité = ordre d'entrée en zone négociable)
  - DEFER_ALL     : aucun contrat n'est gelé (surcharge majeure)

La décision et la décomposition sont tracées dans une tranche gelée
unique (1 batch pour N contrats), avec event GATE_DECISION P3_COLLECTIVE.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from pilotage_flux.events import EventType, append_event
from pilotage_flux.flux.buffers import (
    BufferSpec,
    apply_buffer_to_capacity,
    get_safety_factor,
    get_saturation_limits,
    little_buffer_for_bottleneck,
)
from pilotage_flux.flux.contracts import (
    fetch_contract,
    fetch_version,
    get_candidates_in_version,
)
from pilotage_flux.flux.freeze import create_freeze_batch
from pilotage_flux.gates.p3 import (
    DECISION_FREEZE,
    DECISION_PARTIAL_FREEZE,
    DECISION_RENEGOTIATE,
    P3CriterionResult,
    evaluate_p3_for_contract,
)
from pilotage_flux.zones import ZONE_GELEE, move_candidate_to_zone


DECISION_FREEZE_ALL = "FREEZE_ALL"
DECISION_DEFER_ALL = "DEFER_ALL"


@dataclass(frozen=True)
class ContractLoadProfile:
    """Charge cumulée d'un contrat par poste."""

    contract_id: str
    version: int
    load_by_workstation: dict[str, float]
    total_quantity: float


@dataclass
class CollectiveResult:
    horizon_start: str
    horizon_end: str
    decision: str  # FREEZE_ALL | PARTIAL_FREEZE | DEFER_ALL
    frozen_contracts: list[str] = field(default_factory=list)
    deferred_contracts: list[str] = field(default_factory=list)
    rejected_contracts: list[tuple[str, str]] = field(default_factory=list)
    # (contract_id, reason) pour les contrats refusés sur critère individuel
    bottleneck_workstation: str | None = None  # Le goulot principal
    bottleneck_load: float = 0.0
    bottleneck_capacity: float = 0.0
    # L10.3 — multi-goulot : tous les postes saturés (ratio ≥ threshold)
    bottleneck_workstations: list[tuple[str, float, float, float]] = field(
        default_factory=list
    )
    # liste de (ws_id, load, capacity, ratio) sorted desc par ratio
    # L10.5 — tampons + classification Little
    buffers: list[BufferSpec] = field(default_factory=list)
    saturation_classes: dict[str, str] = field(default_factory=dict)
    # workstation_id -> 'safe' | 'warn' | 'block' | 'defer'
    batch_id: str | None = None


def _horizon_capacity_minutes(
    conn: sqlite3.Connection, workstation_id: str,
    horizon_start: str, horizon_end: str,
) -> float:
    """Capacité totale (en minutes) du poste sur la période horizon."""
    try:
        d_start = date.fromisoformat(horizon_start)
        d_end = date.fromisoformat(horizon_end)
    except ValueError:
        return 0.0
    cal = conn.execute(
        "SELECT daily_minutes, working_days FROM calendars LIMIT 1"
    ).fetchone()
    if cal is None:
        return 0.0
    daily_min = int(cal["daily_minutes"])
    working_days = (cal["working_days"] or "mon,tue,wed,thu,fri").split(",")
    day_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    working_idx = {day_map[d.strip()] for d in working_days if d.strip() in day_map}
    horizon_min = 0
    cur = d_start
    while cur <= d_end:
        if cur.weekday() in working_idx:
            horizon_min += daily_min
        cur = cur.fromordinal(cur.toordinal() + 1)
    row = conn.execute(
        """
        SELECT value_num FROM parameters
        WHERE scope = 'workstation' AND scope_ref = ?
          AND name = 'capacity_factor' AND valid_to IS NULL
        ORDER BY version DESC LIMIT 1
        """,
        (workstation_id,),
    ).fetchone()
    factor = float(row["value_num"]) if row and row["value_num"] is not None else 1.0
    return horizon_min * factor


def _load_profile(
    conn: sqlite3.Connection, contract_id: str, version: int
) -> ContractLoadProfile:
    candidates = get_candidates_in_version(conn, contract_id, version)
    load_by_ws: dict[str, float] = {}
    total_qty = 0.0
    for cand in candidates:
        qty = float(cand["qty_in_contract"])
        total_qty += qty
        ops = conn.execute(
            "SELECT workstation_id, unit_time_min FROM routing_operations "
            "WHERE article_id = ?",
            (cand["article_id"],),
        ).fetchall()
        for op in ops:
            load_by_ws.setdefault(op["workstation_id"], 0.0)
            load_by_ws[op["workstation_id"]] += (
                float(op["unit_time_min"]) * qty
            )
    return ContractLoadProfile(
        contract_id=contract_id, version=version,
        load_by_workstation=load_by_ws,
        total_quantity=total_qty,
    )


def _bottleneck_workstation_from_profiles(
    conn: sqlite3.Connection,
    profiles: list[ContractLoadProfile],
    horizon_start: str,
    horizon_end: str,
) -> str | None:
    """Identifie le poste avec le taux d'utilisation cumulé le plus élevé
    (load_cumulé / capacité)."""
    ranked = identify_bottlenecks(
        conn, profiles, horizon_start, horizon_end, threshold_ratio=0.0,
    )
    return ranked[0][0] if ranked else None


def identify_bottlenecks(
    conn: sqlite3.Connection,
    profiles: list[ContractLoadProfile],
    horizon_start: str,
    horizon_end: str,
    *,
    threshold_ratio: float = 0.8,
) -> list[tuple[str, float, float, float]]:
    """L10.3 — Renvoie TOUS les postes saturés sur l'horizon, ordonnés par
    ratio load/capacity décroissant.

    Format : liste de (ws_id, load_min, capacity_min, ratio). Filtré sur
    `ratio >= threshold_ratio` (défaut 0.8 = 80% d'utilisation).
    """
    cumul: dict[str, float] = {}
    for p in profiles:
        for ws, load in p.load_by_workstation.items():
            cumul.setdefault(ws, 0.0)
            cumul[ws] += load
    out: list[tuple[str, float, float, float]] = []
    for ws, load in cumul.items():
        capa = _horizon_capacity_minutes(conn, ws, horizon_start, horizon_end)
        if capa <= 0:
            ratio = float("inf")
        else:
            ratio = load / capa
        if ratio >= threshold_ratio:
            out.append((ws, float(load), float(capa), float(ratio)))
    out.sort(key=lambda t: t[3], reverse=True)
    return out


def evaluate_p3_collective(
    conn: sqlite3.Connection,
    contract_ids: list[str],
) -> tuple[
    list[tuple[str, list[P3CriterionResult]]],
    list[ContractLoadProfile],
    str,
    float,
    float,
]:
    """Évalue les critères individuels P3 sur chaque contrat + la cohérence
    collective sur le goulot. Pas d'effet de bord.

    Renvoie :
      - per_contract_criteria : critères individuels P3 par contrat
      - profiles              : profils de charge par contrat
      - bottleneck_ws         : poste goulot collectif
      - cumul_load            : charge cumulée sur le goulot (minutes)
      - capacity              : capacité disponible sur le goulot (minutes)
    """
    per_contract: list[tuple[str, list[P3CriterionResult]]] = []
    profiles: list[ContractLoadProfile] = []
    horizon_start: str | None = None
    horizon_end: str | None = None

    for cid in contract_ids:
        contract = fetch_contract(conn, cid)
        if contract is None:
            per_contract.append((cid, [
                P3CriterionResult(
                    rule_id="R-P3-00", criterion="contract_exists",
                    outcome="BLOCK",
                    explanation=f"Contrat {cid} introuvable",
                )
            ]))
            continue
        if horizon_start is None:
            horizon_start = contract.horizon_start
            horizon_end = contract.horizon_end
        criteria = evaluate_p3_for_contract(conn, cid)
        per_contract.append((cid, criteria))
        if all(c.outcome == "PASS" for c in criteria):
            profiles.append(_load_profile(conn, cid, contract.current_version))

    bottleneck = None
    cumul_load = 0.0
    capacity = 0.0
    if profiles and horizon_start and horizon_end:
        bottleneck = _bottleneck_workstation_from_profiles(
            conn, profiles, horizon_start, horizon_end
        )
        if bottleneck:
            cumul_load = sum(
                p.load_by_workstation.get(bottleneck, 0.0) for p in profiles
            )
            capacity = _horizon_capacity_minutes(
                conn, bottleneck, horizon_start, horizon_end
            )
    return per_contract, profiles, bottleneck or "", cumul_load, capacity


def evaluate_p3_collective_with_multi_bottlenecks(
    conn: sqlite3.Connection,
    contract_ids: list[str],
    *,
    bottleneck_threshold_ratio: float = 0.8,
) -> tuple[
    list[tuple[str, list[P3CriterionResult]]],
    list[ContractLoadProfile],
    list[tuple[str, float, float, float]],
]:
    """Variante de evaluate_p3_collective qui renvoie TOUS les postes saturés.

    Renvoie (per_contract, profiles, bottlenecks) où bottlenecks est la liste
    de (ws_id, load, capacity, ratio) triée par ratio décroissant.
    """
    per_contract, profiles, _, _, _ = evaluate_p3_collective(conn, contract_ids)
    if not profiles:
        return per_contract, profiles, []
    horizon_start, horizon_end = "", ""
    for cid in contract_ids:
        c = fetch_contract(conn, cid)
        if c is not None:
            horizon_start, horizon_end = c.horizon_start, c.horizon_end
            break
    bottlenecks = identify_bottlenecks(
        conn, profiles, horizon_start, horizon_end,
        threshold_ratio=bottleneck_threshold_ratio,
    )
    return per_contract, profiles, bottlenecks


def run_p3_collective_freeze(
    conn: sqlite3.Connection,
    contract_ids: list[str],
    *,
    cycle_id: str | None = None,
    actor: str = "gate.p3.collective",
) -> CollectiveResult:
    """Évalue et gèle un ensemble de contrats sur le même horizon, avec
    contrôle collectif de charge sur le poste goulot.

    Stratégie :
      1. Critères individuels P3 sur chaque contrat (R-P3-01..04).
      2. Contrats refusés au critère individuel → rejected_contracts.
      3. Contrats restants : check charge cumulée sur goulot.
         - load ≤ capacity   → FREEZE_ALL (1 tranche gelée commune).
         - load > capacity   → ordre par priorité (= ordre d'entrée
           candidate), inclure ceux qui rentrent jusqu'à saturation
           (PARTIAL_FREEZE), le reste en deferred_contracts.
         - aucun contrat ne tient (load > capacity même pour le 1er) →
           DEFER_ALL.
      4. Émet 1 event GATE_DECISION P3_COLLECTIVE + 1 freeze_batch unique
         pour les contrats gelés.
    """
    if not contract_ids:
        raise ValueError("contract_ids ne peut pas être vide")

    conn.execute("BEGIN")
    try:
        per_criteria, profiles, bottleneck, cumul_load, capacity = (
            evaluate_p3_collective(conn, contract_ids)
        )
        # Index par contract_id pour faciliter lookup
        eligible_ids = {p.contract_id for p in profiles}
        rejected: list[tuple[str, str]] = []
        for cid, crits in per_criteria:
            if cid not in eligible_ids:
                reasons = "; ".join(
                    f"{c.rule_id}={c.outcome}" for c in crits
                )
                rejected.append((cid, reasons))

        # Si aucun contrat éligible
        contract_obj = None
        if profiles:
            contract_obj = fetch_contract(conn, profiles[0].contract_id)

        horizon_start = contract_obj.horizon_start if contract_obj else ""
        horizon_end = contract_obj.horizon_end if contract_obj else ""

        if not profiles:
            decision = DECISION_DEFER_ALL
            result = CollectiveResult(
                horizon_start=horizon_start, horizon_end=horizon_end,
                decision=decision,
                deferred_contracts=[c for c, _ in per_criteria],
                rejected_contracts=rejected,
                bottleneck_workstation=None,
                bottleneck_load=0.0,
                bottleneck_capacity=0.0,
            )
            conn.execute(
                """
                INSERT INTO gate_decisions_v1
                    (gate, subject_type, subject_id, cycle_id, decision,
                     explanation)
                VALUES ('P3_COLLECTIVE', 'horizon', ?, ?, ?, ?)
                """,
                (
                    f"{horizon_start}_{horizon_end}", cycle_id, decision,
                    f"Tous contrats refusés ; rejected={rejected}",
                ),
            )
            conn.execute("COMMIT")
            return result

        # Ordre de priorité : ordre d'entrée en candidates (FIFO sur created_at
        # du premier candidate) — proxy raisonnable pour "qui négocie depuis
        # le plus longtemps".
        def _priority_key(p: ContractLoadProfile) -> str:
            row = conn.execute(
                """
                SELECT MIN(co.created_at) AS first_at
                FROM flux_contract_links l
                JOIN candidate_orders co ON co.candidate_id = l.candidate_id
                WHERE l.contract_id = ? AND l.version = ?
                """,
                (p.contract_id, p.version),
            ).fetchone()
            return row["first_at"] if row and row["first_at"] else "9999"

        profiles_sorted = sorted(profiles, key=_priority_key)

        # L10.3 : identifie les goulots (top contenders à ≥10%)
        multi_bottlenecks = identify_bottlenecks(
            conn, profiles, horizon_start, horizon_end,
            threshold_ratio=0.10,
        )

        # L10.5 — Décision Little-aware avec tampons sur les goulots
        limits = get_saturation_limits(conn)
        safety = get_safety_factor(conn)

        # Calcule capacité effective (tampon réservé) pour chaque WS goulot
        # Un poste est "goulot" si son ratio brut ≥ warn threshold
        buffer_specs: list[BufferSpec] = []
        sat_classes: dict[str, str] = {}
        effective_capa_by_ws: dict[str, float] = {}
        for ws, load, raw_capa, ratio_raw in multi_bottlenecks:
            is_bn = ratio_raw >= limits.warn
            eff_capa = apply_buffer_to_capacity(raw_capa, is_bn, safety)
            effective_capa_by_ws[ws] = eff_capa
            eff_ratio = (load / eff_capa) if eff_capa > 0 else float("inf")
            sat_classes[ws] = limits.classify(eff_ratio)
            if is_bn:
                buffer_specs.append(
                    little_buffer_for_bottleneck(ws, raw_capa, safety)
                )

        # Ratio effectif au goulot principal après tampon
        eff_capacity = effective_capa_by_ws.get(bottleneck, capacity) if bottleneck else capacity
        eff_ratio_main = (
            (cumul_load / eff_capacity) if eff_capacity > 0 else float("inf")
        )
        main_class = limits.classify(eff_ratio_main) if bottleneck else "safe"

        if main_class in ("safe", "warn") or not bottleneck:
            # safe ou warn → FREEZE_ALL (warn est tracé mais ne refuse pas)
            decision = DECISION_FREEZE_ALL
            frozen = [p.contract_id for p in profiles_sorted]
            deferred: list[str] = []
        else:
            # block / defer → PARTIAL_FREEZE jusqu'à effective_capacity
            decision = DECISION_PARTIAL_FREEZE
            running_load = 0.0
            frozen = []
            deferred = []
            for p in profiles_sorted:
                p_load = p.load_by_workstation.get(bottleneck, 0.0)
                if running_load + p_load <= eff_capacity:
                    running_load += p_load
                    frozen.append(p.contract_id)
                else:
                    deferred.append(p.contract_id)
            if not frozen:
                decision = DECISION_DEFER_ALL

        result = CollectiveResult(
            horizon_start=horizon_start, horizon_end=horizon_end,
            decision=decision,
            frozen_contracts=frozen,
            deferred_contracts=deferred,
            rejected_contracts=rejected,
            bottleneck_workstation=bottleneck or None,
            bottleneck_load=cumul_load,
            bottleneck_capacity=capacity,
            bottleneck_workstations=multi_bottlenecks,
            buffers=buffer_specs,
            saturation_classes=sat_classes,
        )

        explanation = (
            f"bottleneck={bottleneck or 'n/a'} "
            f"load={cumul_load:.1f} capacity={capacity:.1f} "
            f"multi_bottlenecks={len(multi_bottlenecks)} "
            f"frozen={frozen} deferred={deferred} rejected={rejected}"
        )

        conn.execute(
            """
            INSERT INTO gate_decisions_v1
                (gate, subject_type, subject_id, cycle_id, decision,
                 explanation)
            VALUES ('P3_COLLECTIVE', 'horizon', ?, ?, ?, ?)
            """,
            (
                f"{horizon_start}_{horizon_end}", cycle_id, decision, explanation,
            ),
        )

        if not frozen:
            conn.execute("COMMIT")
            return result

        # Émet l'événement P3_COLLECTIVE et crée UNE tranche gelée commune
        event_id = append_event(
            conn,
            aggregate_type="flux_contract_group",
            aggregate_id=f"{horizon_start}_{horizon_end}",
            event_type=EventType.GATE_DECISION,
            payload={
                "gate": "P3_COLLECTIVE",
                "decision": decision,
                "frozen": frozen,
                "deferred": deferred,
                "rejected": [c for c, _ in rejected],
                "bottleneck": bottleneck,
                "bottleneck_load": cumul_load,
                "bottleneck_capacity": capacity,
            },
            actor=actor,
            source_module="gate.p3_collective",
        )

        # Crée la tranche gelée avec TOUS les contrats frozen
        frozen_pairs: list[tuple[str, int]] = []
        for p in profiles_sorted:
            if p.contract_id in frozen:
                frozen_pairs.append((p.contract_id, p.version))
        batch = create_freeze_batch(
            conn,
            contracts=frozen_pairs,
            horizon_start=horizon_start,
            horizon_end=horizon_end,
            decision=decision,
            cycle_id=cycle_id,
            explanation=explanation,
            event_id=event_id,
        )

        # Transition zone négociable -> gelée pour les candidates des contrats frozen
        for p in profiles_sorted:
            if p.contract_id not in frozen:
                continue
            candidates = get_candidates_in_version(
                conn, p.contract_id, p.version
            )
            for cand in candidates:
                move_candidate_to_zone(
                    conn,
                    cand["candidate_id"],
                    ZONE_GELEE,
                    decision=decision,
                    rule_ref="gate.p3.collective",
                    explanation=f"Freeze batch {batch.batch_id}",
                    cycle_id=cycle_id,
                    actor=actor,
                    event_id=event_id,
                )
            # Statut du contrat -> frozen
            conn.execute(
                "UPDATE flux_contracts SET status = 'frozen', "
                "updated_at = datetime('now') WHERE contract_id = ?",
                (p.contract_id,),
            )

        result.batch_id = batch.batch_id
        conn.execute("COMMIT")
        return result
    except Exception:
        conn.execute("ROLLBACK")
        raise
