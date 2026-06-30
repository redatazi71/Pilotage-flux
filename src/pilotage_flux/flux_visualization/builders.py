"""Builders des 5 flux de visualisation.

Chaque builder émet un graphe (nodes + edges) selon une grammaire
commune :

  Node = {id, label, kind, attributes}
  Edge = {source, target, kind, label, attributes}

Le `kind` distingue le type sémantique du nœud (workstation, of,
lot, contract, deviation, decision, gate...). Le caller peut
filtrer ou colorer par kind. Les `attributes` portent des
données libres (counts, scores, statuts...).

API uniforme : `build_flux_<x>(conn) -> FluxGraph`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass(frozen=True)
class FluxNode:
    id: str
    label: str
    kind: str
    attributes: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "label": self.label,
            "kind": self.kind, "attributes": dict(self.attributes),
        }


@dataclass(frozen=True)
class FluxEdge:
    source: str
    target: str
    kind: str
    label: str | None = None
    attributes: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source": self.source, "target": self.target,
            "kind": self.kind, "label": self.label,
            "attributes": dict(self.attributes),
        }


@dataclass
class FluxGraph:
    flux: str                    # 'physique' | 'information' | etc.
    nodes: list[FluxNode] = field(default_factory=list)
    edges: list[FluxEdge] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "flux": self.flux,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }

    @property
    def n_nodes(self) -> int:
        return len(self.nodes)

    @property
    def n_edges(self) -> int:
        return len(self.edges)


# ---------------------------------------------------------------------
# Flux physique : matières / OF / lots / WIP / postes / goulots
# ---------------------------------------------------------------------


def build_flux_physique(conn: sqlite3.Connection) -> FluxGraph:
    """Flux physique = circulation réelle des matières et OF.

    Nodes : workstations (avec WIP courant), articles, OF (avec
            statut), lots de pegging (réservations matière).
    Edges : routing (article → ws via operations), pegging
            (composant → OF), précédences WS (séquence_idx).
    """
    g = FluxGraph(flux="physique")

    ws_rows = conn.execute(
        "SELECT workstation_id, label, sequence_idx "
        "FROM workstations ORDER BY sequence_idx"
    ).fetchall()
    for r in ws_rows:
        # WIP courant = OFs en in_progress sur ce poste
        wip_row = conn.execute(
            "SELECT COUNT(*) AS n FROM order_operations op "
            "JOIN manufacturing_orders mo ON mo.of_id = op.of_id "
            "WHERE op.workstation_id = ? AND op.status = 'in_progress' "
            "AND mo.status IN ('launched', 'in_progress')",
            (r["workstation_id"],),
        ).fetchone()
        g.nodes.append(FluxNode(
            id=f"ws:{r['workstation_id']}",
            label=f"{r['workstation_id']} — {r['label']}",
            kind="workstation",
            attributes={
                "sequence_idx": r["sequence_idx"],
                "wip": int(wip_row["n"]) if wip_row else 0,
            },
        ))

    # Précédences entre WS (basé sur sequence_idx)
    for i in range(len(ws_rows) - 1):
        g.edges.append(FluxEdge(
            source=f"ws:{ws_rows[i]['workstation_id']}",
            target=f"ws:{ws_rows[i+1]['workstation_id']}",
            kind="ws_precedence",
        ))

    # OFs et leur poste courant
    of_rows = conn.execute(
        "SELECT of_id, article_id, status, quantity "
        "FROM manufacturing_orders "
        "WHERE status NOT IN ('closed', 'cancelled') ORDER BY of_id"
    ).fetchall()
    for r in of_rows:
        g.nodes.append(FluxNode(
            id=f"of:{r['of_id']}",
            label=f"{r['of_id']} ({r['article_id']})",
            kind="of",
            attributes={
                "status": r["status"],
                "quantity": float(r["quantity"]),
            },
        ))
        # Lien OF → WS courant (op active)
        op_row = conn.execute(
            "SELECT workstation_id FROM order_operations "
            "WHERE of_id = ? AND status IN ('planned', 'in_progress') "
            "ORDER BY sequence_idx LIMIT 1",
            (r["of_id"],),
        ).fetchone()
        if op_row:
            g.edges.append(FluxEdge(
                source=f"of:{r['of_id']}",
                target=f"ws:{op_row['workstation_id']}",
                kind="of_at_ws",
            ))

    return g


# ---------------------------------------------------------------------
# Flux information : prévisions / ordres / contrats / événements
# ---------------------------------------------------------------------


def build_flux_information(conn: sqlite3.Connection) -> FluxGraph:
    """Flux information = transformation des données client → contrats.

    Nodes : SO (demande), candidate_orders (hypothèse), flux_contracts
            (engagement collectif), expected_events (mesures attendues).
    Edges : SO → candidate → contract → events.
    """
    g = FluxGraph(flux="information")

    so_rows = conn.execute(
        "SELECT sales_order_id, article_id, quantity, due_date "
        "FROM sales_orders ORDER BY sales_order_id"
    ).fetchall()
    for r in so_rows:
        g.nodes.append(FluxNode(
            id=f"so:{r['sales_order_id']}",
            label=f"SO {r['sales_order_id']}",
            kind="sales_order",
            attributes={
                "article_id": r["article_id"],
                "quantity": float(r["quantity"]),
                "due_date": r["due_date"],
            },
        ))

    cand_rows = conn.execute(
        "SELECT candidate_id, sales_order_id, article_id, status, zone "
        "FROM candidate_orders ORDER BY candidate_id"
    ).fetchall()
    for r in cand_rows:
        g.nodes.append(FluxNode(
            id=f"cand:{r['candidate_id']}",
            label=f"CAND {r['candidate_id']}",
            kind="candidate",
            attributes={"status": r["status"], "zone": r["zone"]},
        ))
        if r["sales_order_id"]:
            g.edges.append(FluxEdge(
                source=f"so:{r['sales_order_id']}",
                target=f"cand:{r['candidate_id']}",
                kind="so_to_candidate",
            ))

    contract_rows = conn.execute(
        "SELECT contract_id, status FROM flux_contracts "
        "ORDER BY contract_id"
    ).fetchall()
    for r in contract_rows:
        g.nodes.append(FluxNode(
            id=f"fx:{r['contract_id']}",
            label=f"FX {r['contract_id']}",
            kind="flux_contract",
            attributes={"status": r["status"]},
        ))
        # Liens contract → candidates (version max)
        link_rows = conn.execute(
            "SELECT candidate_id FROM flux_contract_links "
            "WHERE contract_id = ? "
            "AND version = (SELECT MAX(version) FROM flux_contract_links "
            "               WHERE contract_id = ?)",
            (r["contract_id"], r["contract_id"]),
        ).fetchall()
        for l in link_rows:
            g.edges.append(FluxEdge(
                source=f"cand:{l['candidate_id']}",
                target=f"fx:{r['contract_id']}",
                kind="candidate_in_contract",
            ))

    n_expected = conn.execute(
        "SELECT COUNT(*) AS n FROM expected_events"
    ).fetchone()
    if n_expected and n_expected["n"]:
        g.nodes.append(FluxNode(
            id="expected_events",
            label=f"Expected events ({n_expected['n']})",
            kind="event_pool",
            attributes={"count": int(n_expected["n"])},
        ))

    return g


# ---------------------------------------------------------------------
# Flux décision : portes / decisions / scores / actions
# ---------------------------------------------------------------------


def build_flux_decision(conn: sqlite3.Connection) -> FluxGraph:
    """Flux décision = portes P1-P4 et leurs décisions, événements
    déviation et leurs niveaux Delta."""
    g = FluxGraph(flux="decision")

    # Portes P1..P4 comme nœuds de référence
    for gate, label in (
        ("P1", "P1 — Entrée libre (fonctionnelle)"),
        ("P2", "P2 — Libre → Négociable (mensuelle)"),
        ("P3", "P3 — Négociable → Gelée (hebdo)"),
        ("P3inv", "P3 inverse — Retour/Fragment"),
        ("P4", "P4 — Sortie clôture (fonctionnelle)"),
    ):
        g.nodes.append(FluxNode(
            id=f"gate:{gate}", label=label, kind="gate",
        ))

    # Compte les gate_decisions par gate
    gd_rows = conn.execute(
        "SELECT gate, decision, COUNT(*) AS n FROM gate_decisions "
        "GROUP BY gate, decision"
    ).fetchall()
    for r in gd_rows:
        gate_id = f"gate:{r['gate']}"
        dec_id = f"gd:{r['gate']}:{r['decision']}"
        g.nodes.append(FluxNode(
            id=dec_id,
            label=f"{r['decision']} ({r['n']})",
            kind="gate_decision",
            attributes={"count": int(r["n"])},
        ))
        g.edges.append(FluxEdge(
            source=gate_id, target=dec_id,
            kind="gate_emits",
        ))

    # Niveaux Delta (L1..L6) avec leur compteur
    lvl_rows = conn.execute(
        "SELECT niveau_code, label, cadrage_level FROM delta_action_levels "
        "ORDER BY ordre"
    ).fetchall()
    for r in lvl_rows:
        count_row = conn.execute(
            "SELECT COUNT(*) AS n FROM delta_decisions "
            "WHERE niveau_code = ?",
            (r["niveau_code"],),
        ).fetchone()
        n = int(count_row["n"]) if count_row else 0
        g.nodes.append(FluxNode(
            id=f"niveau:{r['niveau_code']}",
            label=f"{r['niveau_code']} {r['label']} ({n})",
            kind="delta_level",
            attributes={
                "cadrage_level": int(r["cadrage_level"]),
                "count": n,
            },
        ))

    # Déviations → niveau Delta
    dd_rows = conn.execute(
        "SELECT deviation_id, niveau_code, status FROM delta_decisions "
        "WHERE deviation_id IS NOT NULL LIMIT 200"
    ).fetchall()
    seen_devs: set[int] = set()
    for r in dd_rows:
        dev_id = int(r["deviation_id"])
        if dev_id not in seen_devs:
            g.nodes.append(FluxNode(
                id=f"dev:{dev_id}",
                label=f"DEV {dev_id}",
                kind="deviation",
            ))
            seen_devs.add(dev_id)
        g.edges.append(FluxEdge(
            source=f"dev:{dev_id}",
            target=f"niveau:{r['niveau_code']}",
            kind="deviation_to_niveau",
            attributes={"status": r["status"]},
        ))

    return g


# ---------------------------------------------------------------------
# Flux documentaire : versions articles / gammes / BOM / contrats
# ---------------------------------------------------------------------


def build_flux_documentaire(conn: sqlite3.Connection) -> FluxGraph:
    """Flux documentaire = versionnage des objets de référence."""
    g = FluxGraph(flux="documentaire")

    # Articles
    art_rows = conn.execute(
        "SELECT article_id, label FROM articles ORDER BY article_id"
    ).fetchall()
    for r in art_rows:
        g.nodes.append(FluxNode(
            id=f"art:{r['article_id']}",
            label=f"ART {r['article_id']}",
            kind="article",
        ))

    # BOM lines : composant → parent
    bom_rows = conn.execute(
        "SELECT parent_article, child_article, quantity "
        "FROM bom_lines ORDER BY parent_article, child_article"
    ).fetchall()
    for r in bom_rows:
        g.edges.append(FluxEdge(
            source=f"art:{r['child_article']}",
            target=f"art:{r['parent_article']}",
            kind="bom_composition",
            label=f"×{r['quantity']:.1f}",
        ))

    # Versions de pondération MACRS (weight_versions) avec leur status
    wv_rows = conn.execute(
        "SELECT weight_version_id, label, status FROM weight_versions "
        "ORDER BY weight_version_id"
    ).fetchall()
    for r in wv_rows:
        g.nodes.append(FluxNode(
            id=f"wv:{r['weight_version_id']}",
            label=f"WV {r['label']}",
            kind="weight_version",
            attributes={"status": r["status"]},
        ))

    # Versions de flux_contract
    ver_rows = conn.execute(
        "SELECT contract_id, version FROM flux_contract_versions "
        "ORDER BY contract_id, version"
    ).fetchall()
    for r in ver_rows:
        g.nodes.append(FluxNode(
            id=f"fxv:{r['contract_id']}:{r['version']}",
            label=f"{r['contract_id']} v{r['version']}",
            kind="contract_version",
            attributes={"version": int(r["version"])},
        ))

    return g


# ---------------------------------------------------------------------
# Flux qualité : contrôles / NC / libérations
# ---------------------------------------------------------------------


def build_flux_qualite(conn: sqlite3.Connection) -> FluxGraph:
    """Flux qualité = événements qualité, NC, libérations.

    Nodes : OFs avec un événement qualité, NCs ouvertes/résolues.
    Edges : OF → quality_event (création), quality_event → OF
            (libération si applicable).
    """
    g = FluxGraph(flux="qualite")

    qe_rows = conn.execute(
        "SELECT quality_event_id, of_id, of_op_id, event_type, "
        "       severity, qty_concerned "
        "FROM quality_events ORDER BY quality_event_id"
    ).fetchall()
    for r in qe_rows:
        qe_node_id = f"qe:{r['quality_event_id']}"
        g.nodes.append(FluxNode(
            id=qe_node_id,
            label=f"QE {r['quality_event_id']} ({r['event_type']})",
            kind="quality_event",
            attributes={
                "severity": r["severity"],
                "event_type": r["event_type"],
                "qty_concerned": float(r["qty_concerned"] or 0),
            },
        ))
        if r["of_id"]:
            of_node_id = f"of:{r['of_id']}"
            if not any(n.id == of_node_id for n in g.nodes):
                g.nodes.append(FluxNode(
                    id=of_node_id, label=r["of_id"], kind="of",
                ))
            g.edges.append(FluxEdge(
                source=of_node_id, target=qe_node_id,
                kind="of_has_quality_event",
            ))

    # Production contracts breached : alertes qualité doctrinales
    pc_breach = conn.execute(
        "SELECT pc_id, of_id, breach_dimensions FROM production_contracts "
        "WHERE status = 'breached'"
    ).fetchall()
    for r in pc_breach:
        pc_id = f"pc_breach:{r['pc_id']}"
        g.nodes.append(FluxNode(
            id=pc_id,
            label=f"PC {r['pc_id']} breached",
            kind="pc_breach",
            attributes={"dimensions": r["breach_dimensions"]},
        ))
        of_node_id = f"of:{r['of_id']}"
        if not any(n.id == of_node_id for n in g.nodes):
            g.nodes.append(FluxNode(
                id=of_node_id, label=r["of_id"], kind="of",
            ))
        g.edges.append(FluxEdge(
            source=of_node_id, target=pc_id,
            kind="of_breached_pc",
        ))

    return g


# ---------------------------------------------------------------------
# All-in-one
# ---------------------------------------------------------------------


def build_all_flux(conn: sqlite3.Connection) -> dict[str, FluxGraph]:
    """Construit les 5 flux et renvoie un dict indexé par nom."""
    return {
        "physique": build_flux_physique(conn),
        "information": build_flux_information(conn),
        "decision": build_flux_decision(conn),
        "documentaire": build_flux_documentaire(conn),
        "qualite": build_flux_qualite(conn),
    }
