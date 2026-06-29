"""P3 inverse - cadrage §7 bis.6.

Sortie controlee de la zone gelee, en deux formes mutuellement exclusives :

  Forme A - RETOUR_NEGOCIABLE :
    Autorisee si l'OF associe au candidate est 'created' (gele non lance).
    L'OF est annule, le candidate revient en zone negociable.

  Forme B - FRAGMENT :
    Autorisee si l'OF est 'launched' ou 'in_progress' (gele partiellement lance).
    L'OF source garde la portion executee, un nouvel OF fragment est cree pour
    la portion restante avec filiation explicite (parent_of_id).
    Conservation : fragment.quantity + source.quantity_apres = source.quantity_avant.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pilotage_flux.events import EventType, append_event
from pilotage_flux.zones import (
    ZONE_GELEE,
    ZONE_NEGOCIABLE,
    current_zone,
    move_candidate_to_zone,
)


DECISION_RETURN = "RETOUR_NEGOCIABLE"
DECISION_FRAGMENT = "FRAGMENT"


@dataclass(frozen=True)
class ReturnResult:
    candidate_id: str
    cancelled_of_id: str | None
    event_id: int


@dataclass(frozen=True)
class FragmentResult:
    source_of_id: str
    fragment_of_id: str
    fragment_quantity: float
    source_quantity_after: float
    event_id: int


def _of_for_candidate(
    conn: sqlite3.Connection, candidate_id: str
) -> sqlite3.Row | None:
    """Retrouve l'OF actif associe a un candidate (status != cancelled)."""
    return conn.execute(
        """
        SELECT of_id, status, quantity FROM manufacturing_orders
        WHERE candidate_id = ? AND status != 'cancelled'
        ORDER BY of_id ASC
        LIMIT 1
        """,
        (candidate_id,),
    ).fetchone()


# -----------------------------------------------------------------------
# Forme A : RETOUR_NEGOCIABLE
# -----------------------------------------------------------------------

def return_to_negociable(
    conn: sqlite3.Connection,
    candidate_id: str,
    *,
    reason: str,
    actor: str = "gate.p3_inverse",
    cycle_id: str | None = None,
) -> ReturnResult:
    """Forme A : ramene un candidate gele en zone negociable.

    L'OF associe doit etre en status='created' (non lance). Il est passe
    en 'cancelled' et un event OF_CANCELLED + OF_RETURNED_NEGOCIABLE sont
    emis. Le candidate transitionne gelee -> negociable.

    Leve ValueError si :
      - candidate non en zone gelee
      - OF deja lance (utiliser fragment_of pour la forme B)
    """
    zone = current_zone(conn, candidate_id)
    if zone != ZONE_GELEE:
        raise ValueError(
            f"Candidate {candidate_id} en zone {zone!r}, attendu 'gelee'"
        )

    of_row = _of_for_candidate(conn, candidate_id)
    if of_row is not None and of_row["status"] != "created":
        raise ValueError(
            f"OF {of_row['of_id']} en statut {of_row['status']!r} : "
            f"forme A interdite ; utiliser fragment_of (forme B) si lance"
        )

    conn.execute("BEGIN")
    try:
        cancelled_of_id: str | None = None
        if of_row is not None:
            of_id = of_row["of_id"]
            conn.execute(
                "UPDATE manufacturing_orders SET status = 'cancelled' WHERE of_id = ?",
                (of_id,),
            )
            append_event(
                conn,
                aggregate_type="manufacturing_order",
                aggregate_id=of_id,
                event_type=EventType.OF_CANCELLED,
                payload={"reason": reason, "from_status": "created"},
                actor=actor,
                source_module="gate.p3_inverse",
            )
            cancelled_of_id = of_id

        event_id = append_event(
            conn,
            aggregate_type="candidate_order",
            aggregate_id=candidate_id,
            event_type=EventType.OF_RETURNED_NEGOCIABLE,
            payload={
                "reason": reason,
                "cancelled_of": cancelled_of_id,
                "form": "A",
            },
            actor=actor,
            source_module="gate.p3_inverse",
        )

        move_candidate_to_zone(
            conn,
            candidate_id,
            ZONE_NEGOCIABLE,
            decision=DECISION_RETURN,
            rule_ref="gate.p3_inverse.A",
            explanation=reason,
            cycle_id=cycle_id,
            actor=actor,
            event_id=event_id,
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return ReturnResult(
        candidate_id=candidate_id,
        cancelled_of_id=cancelled_of_id,
        event_id=event_id,
    )


# -----------------------------------------------------------------------
# Forme B : FRAGMENT
# -----------------------------------------------------------------------

def _next_fragment_of_id(conn: sqlite3.Connection) -> str:
    """Genere un identifiant OF unique pour un fragment (suite a l'auto-incr)."""
    row = conn.execute(
        "SELECT of_id FROM manufacturing_orders ORDER BY of_id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return "OF-0001"
    last = row["of_id"]
    try:
        n = int(last.split("-")[-1])
    except (ValueError, IndexError):
        n = 0
    return f"OF-{n + 1:04d}"


def fragment_of(
    conn: sqlite3.Connection,
    source_of_id: str,
    *,
    fragment_quantity: float,
    reason: str,
    actor: str = "gate.p3_inverse",
    cycle_id: str | None = None,
) -> FragmentResult:
    """Forme B : fragmente un OF lance/in_progress en deux OFs.

    L'OF source perd `fragment_quantity` unites, qui partent dans un nouvel
    OF fragment (status='created', toutes ops 'pending', parent_of_id=source).

    Le candidate du source reste en zone gelee ; pour le ramener en negociable,
    appliquer return_to_negociable sur le candidate du fragment (qui aura un
    OF en status='created' donc forme A possible).

    Leve ValueError si :
      - OF source inconnu
      - OF source en status='created' (utiliser forme A) ou 'closed'/'cancelled'
      - fragment_quantity <= 0 ou > quantity courante de source
    """
    of_row = conn.execute(
        """
        SELECT of_id, candidate_id, article_id, quantity, status,
               qty_good, qty_scrap
        FROM manufacturing_orders WHERE of_id = ?
        """,
        (source_of_id,),
    ).fetchone()
    if of_row is None:
        raise ValueError(f"OF inconnu : {source_of_id}")
    if of_row["status"] not in ("launched", "in_progress"):
        raise ValueError(
            f"OF {source_of_id} en statut {of_row['status']!r} : forme B "
            f"interdite (autorisee uniquement sur 'launched' ou 'in_progress')"
        )

    fragment_quantity = float(fragment_quantity)
    if fragment_quantity <= 0:
        raise ValueError("fragment_quantity doit etre strictement positif")
    source_qty = float(of_row["quantity"])
    if fragment_quantity >= source_qty:
        raise ValueError(
            f"fragment_quantity ({fragment_quantity}) doit etre < quantite source ({source_qty})"
        )

    new_source_qty = source_qty - fragment_quantity

    conn.execute("BEGIN")
    try:
        # 1. Diminue la quantite de l'OF source
        conn.execute(
            "UPDATE manufacturing_orders SET quantity = ? WHERE of_id = ?",
            (new_source_qty, source_of_id),
        )

        # 2. Cree le nouvel OF fragment
        fragment_id = _next_fragment_of_id(conn)
        conn.execute(
            """
            INSERT INTO manufacturing_orders
                (of_id, candidate_id, article_id, quantity, status, parent_of_id)
            VALUES (?, ?, ?, ?, 'created', ?)
            """,
            (
                fragment_id,
                of_row["candidate_id"],
                of_row["article_id"],
                fragment_quantity,
                source_of_id,
            ),
        )

        # 3. Copie le routing du fragment depuis le routing source (toutes ops 'pending')
        ops = conn.execute(
            """
            SELECT sequence_idx, workstation_id, unit_time_min
            FROM routing_operations WHERE article_id = ?
            ORDER BY sequence_idx ASC
            """,
            (of_row["article_id"],),
        ).fetchall()
        for op in ops:
            conn.execute(
                """
                INSERT INTO order_operations
                    (of_id, sequence_idx, workstation_id, unit_time_min, status)
                VALUES (?, ?, ?, ?, 'pending')
                """,
                (
                    fragment_id,
                    int(op["sequence_idx"]),
                    op["workstation_id"],
                    float(op["unit_time_min"]),
                ),
            )

        # 4. Trace event filiation
        event_id = append_event(
            conn,
            aggregate_type="manufacturing_order",
            aggregate_id=source_of_id,
            event_type=EventType.OF_FRAGMENTED,
            payload={
                "reason": reason,
                "fragment_of": fragment_id,
                "fragment_quantity": fragment_quantity,
                "source_quantity_before": source_qty,
                "source_quantity_after": new_source_qty,
                "form": "B",
            },
            actor=actor,
            source_module="gate.p3_inverse",
        )

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return FragmentResult(
        source_of_id=source_of_id,
        fragment_of_id=fragment_id,
        fragment_quantity=fragment_quantity,
        source_quantity_after=new_source_qty,
        event_id=event_id,
    )


# -----------------------------------------------------------------------
# Requetes filiation
# -----------------------------------------------------------------------

@dataclass(frozen=True)
class LineageNode:
    of_id: str
    article_id: str
    quantity: float
    status: str
    parent_of_id: str | None


def get_lineage(conn: sqlite3.Connection, of_id: str) -> list[LineageNode]:
    """Renvoie la chaine de filiation : source remontante + descendants directs.

    Format : [racine, ..., of_id, ..., fragments_de_of_id]
    """
    # Remonte jusqu'a la racine
    chain_up: list[sqlite3.Row] = []
    current_id = of_id
    while current_id is not None:
        row = conn.execute(
            "SELECT of_id, article_id, quantity, status, parent_of_id "
            "FROM manufacturing_orders WHERE of_id = ?",
            (current_id,),
        ).fetchone()
        if row is None:
            break
        chain_up.insert(0, row)
        current_id = row["parent_of_id"]

    # Descendants directs de of_id (fragments)
    descendants = list(
        conn.execute(
            """
            SELECT of_id, article_id, quantity, status, parent_of_id
            FROM manufacturing_orders WHERE parent_of_id = ?
            ORDER BY of_id ASC
            """,
            (of_id,),
        )
    )

    # Concatene en evitant les doublons
    seen: set[str] = set()
    out: list[LineageNode] = []
    for r in chain_up + descendants:
        if r["of_id"] in seen:
            continue
        seen.add(r["of_id"])
        out.append(
            LineageNode(
                of_id=r["of_id"],
                article_id=r["article_id"],
                quantity=float(r["quantity"]),
                status=r["status"],
                parent_of_id=r["parent_of_id"],
            )
        )
    return out
