"""Point d'entree CLI : `pflux ...`."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from pilotage_flux.db import init_schema, db_session
from pilotage_flux.events import (
    fetch_events,
    EventType,
    append_event,
    reconstruct_of,
)
from pilotage_flux.gates import run_p1_promotion
from pilotage_flux.importers import import_referentials
from pilotage_flux.mes import (
    launch_of,
    start_operation,
    finish_operation,
    close_of,
)
from pilotage_flux.visualization import of_detail_view, workstation_view


app = typer.Typer(
    name="pflux",
    help="Pilotage-flux V0 - APS+MES en pilotage par flux lean.",
    no_args_is_help=True,
)
console = Console()

DEFAULT_RUNS_DIR = Path("data/runs")
DEFAULT_FIXTURES_DIR = Path("data/fixtures")


def _db_path(run: str) -> Path:
    return DEFAULT_RUNS_DIR / f"{run}.db"


@app.command("init-db")
def init_db(
    run: str = typer.Option("default", help="Nom du run (= nom du fichier .db)."),
    drop_existing: bool = typer.Option(
        False, "--drop", help="Supprime la base existante avant de recreer."
    ),
) -> None:
    """Cree une nouvelle base SQLite avec le schema V0."""
    path = _db_path(run)
    init_schema(path, drop_existing=drop_existing)
    with db_session(path) as conn:
        append_event(
            conn,
            aggregate_type="run",
            aggregate_id=run,
            event_type=EventType.GATE_DECISION,
            payload={"action": "db_initialized", "schema": "v0.1"},
            actor="cli",
            source_module="cli.init_db",
        )
    console.print(f"[green]OK[/green] base initialisee : [bold]{path}[/bold]")


@app.command("import-refs")
def import_refs(
    run: str = typer.Option("default", help="Nom du run."),
    fixtures: Path = typer.Option(
        DEFAULT_FIXTURES_DIR, help="Dossier contenant les fixtures CSV."
    ),
) -> None:
    """Importe les referentiels CSV depuis `fixtures/`."""
    path = _db_path(run)
    if not path.exists():
        console.print(f"[red]ERR[/red] base introuvable : {path}. Lancez `pflux init-db` d'abord.")
        raise typer.Exit(code=1)
    with db_session(path) as conn:
        results = import_referentials(conn, fixtures)
        append_event(
            conn,
            aggregate_type="run",
            aggregate_id=run,
            event_type=EventType.GATE_DECISION,
            payload={
                "action": "referentials_imported",
                "counts": {r.table: r.rows_inserted for r in results},
            },
            actor="cli",
            source_module="cli.import_refs",
        )

    tbl = Table(title="Import referentiels")
    tbl.add_column("Table")
    tbl.add_column("Lignes inserees", justify="right")
    tbl.add_column("Fichier manquant ?", justify="center")
    for r in results:
        tbl.add_row(r.table, str(r.rows_inserted), "oui" if r.skipped else "")
    console.print(tbl)


@app.command("events")
def events_cmd(
    run: str = typer.Option("default", help="Nom du run."),
    limit: int = typer.Option(50, help="Nombre max d'evenements affiches."),
) -> None:
    """Affiche les evenements de l'event store."""
    path = _db_path(run)
    if not path.exists():
        console.print(f"[red]ERR[/red] base introuvable : {path}")
        raise typer.Exit(code=1)
    with db_session(path) as conn:
        rows = fetch_events(conn, limit=limit)
    tbl = Table(title=f"event_store ({len(rows)} dernieres lignes)")
    tbl.add_column("id", justify="right")
    tbl.add_column("at", no_wrap=True)
    tbl.add_column("aggregate", no_wrap=True)
    tbl.add_column("type")
    tbl.add_column("payload", overflow="fold")
    for r in rows:
        tbl.add_row(
            str(r["event_id"]),
            r["occurred_at"],
            f"{r['aggregate_type']}/{r['aggregate_id']}",
            r["event_type"],
            r["payload_json"],
        )
    console.print(tbl)


@app.command("plan")
def plan_cmd(
    run: str = typer.Option("default", help="Nom du run."),
) -> None:
    """Execute la porte P1 : CBN, charge/capacite, creation des OF."""
    path = _db_path(run)
    if not path.exists():
        console.print(f"[red]ERR[/red] base introuvable : {path}")
        raise typer.Exit(code=1)

    with db_session(path) as conn:
        outcome = run_p1_promotion(conn)

    # Resume CBN
    if outcome.candidates_created:
        tbl_cnd = Table(title=f"CBN - {len(outcome.candidates_created)} candidate_orders crees")
        tbl_cnd.add_column("candidate_id")
        tbl_cnd.add_column("sales_order_id")
        tbl_cnd.add_column("article_id")
        tbl_cnd.add_column("quantity", justify="right")
        for c in outcome.candidates_created:
            tbl_cnd.add_row(c.candidate_id, c.sales_order_id, c.article_id, str(c.quantity))
        console.print(tbl_cnd)
    else:
        console.print("[yellow]CBN[/yellow] aucun nouveau candidate_order (idempotent).")

    # Charge / capacite
    tbl_load = Table(title="Charge / capacite par poste")
    tbl_load.add_column("workstation_id")
    tbl_load.add_column("label")
    tbl_load.add_column("charge (min)", justify="right")
    tbl_load.add_column("capacite/j (min)", justify="right")
    tbl_load.add_column("surcharge", justify="center")
    for w in outcome.workstation_load:
        tbl_load.add_row(
            w.workstation_id,
            w.label,
            f"{w.load_minutes:.1f}",
            f"{w.daily_capacity_minutes:.0f}",
            "[red]OUI[/red]" if w.is_overloaded else "[green]non[/green]",
        )
    console.print(tbl_load)

    # OF crees
    if outcome.ofs_created:
        tbl_of = Table(title=f"P1 - {len(outcome.ofs_created)} OF crees")
        tbl_of.add_column("of_id")
        tbl_of.add_column("article")
        tbl_of.add_column("qte", justify="right")
        tbl_of.add_column("operations", justify="right")
        tbl_of.add_column("event_id", justify="right")
        for o in outcome.ofs_created:
            tbl_of.add_row(
                o.of_id, o.article_id, str(o.quantity), str(o.operations), str(o.event_id)
            )
        console.print(tbl_of)
    else:
        console.print("[yellow]P1[/yellow] aucun OF cree.")


@app.command("simulate-execution")
def simulate_execution(
    run: str = typer.Option("default", help="Nom du run."),
    of_id: str = typer.Option(..., "--of", help="ID de l'OF a simuler (ex: OF-0001)."),
    yield_rate: float = typer.Option(
        0.95, help="Taux de pieces bonnes pour la simulation (defaut 0.95)."
    ),
) -> None:
    """Simule un parcours complet : lancement -> ops -> cloture pour un OF donne."""
    path = _db_path(run)
    if not path.exists():
        console.print(f"[red]ERR[/red] base introuvable : {path}")
        raise typer.Exit(code=1)

    with db_session(path) as conn:
        # 1. Lancement
        launch = launch_of(conn, of_id)
        console.print(f"[green]LAUNCH[/green] {of_id} -> launched (event {launch.event_id})")

        # 2. Iteration sur les operations dans l'ordre
        ops = conn.execute(
            """
            SELECT of_op_id, sequence_idx, workstation_id
            FROM order_operations
            WHERE of_id = ?
            ORDER BY sequence_idx ASC
            """,
            (of_id,),
        ).fetchall()
        of_qty = conn.execute(
            "SELECT quantity FROM manufacturing_orders WHERE of_id = ?",
            (of_id,),
        ).fetchone()["quantity"]
        qty_good = round(of_qty * yield_rate, 2)
        qty_scrap = round(of_qty - qty_good, 2)

        for op in ops:
            start_operation(conn, op["of_op_id"])
            finish_operation(
                conn,
                op["of_op_id"],
                qty_good=qty_good,
                qty_scrap=qty_scrap,
            )
            console.print(
                f"  [cyan]OP {op['sequence_idx']}[/cyan] {op['workstation_id']} "
                f"-> done ({qty_good} bonnes / {qty_scrap} rebuts)"
            )

        # 3. Cloture P4
        result = close_of(conn, of_id)
        console.print(
            f"[green]CLOSE[/green] {of_id} -> closed "
            f"(bonnes={result.qty_good}, rebuts={result.qty_scrap}, event {result.event_id})"
        )


@app.command("replay")
def replay_cmd(
    run: str = typer.Option("default", help="Nom du run."),
    of_id: str = typer.Option(..., "--of", help="ID de l'OF a reconstruire."),
) -> None:
    """Reconstruit l'etat d'un OF a partir de l'event_store uniquement.

    Demontre que la trajectoire complete est rejouable depuis les evenements.
    """
    path = _db_path(run)
    if not path.exists():
        console.print(f"[red]ERR[/red] base introuvable : {path}")
        raise typer.Exit(code=1)
    with db_session(path) as conn:
        state = reconstruct_of(conn, of_id)

    if state.event_count == 0:
        console.print(f"[yellow]Aucun evenement pour {of_id}[/yellow]")
        return

    tbl = Table(title=f"Reconstruction de {of_id} depuis {state.event_count} evenements")
    tbl.add_column("Champ")
    tbl.add_column("Valeur")
    tbl.add_row("status", state.status)
    tbl.add_row("article_id", str(state.article_id))
    tbl.add_row("quantite planifiee", str(state.quantity))
    tbl.add_row("qte bonnes (rejouee)", str(state.qty_good))
    tbl.add_row("qte rebuts (rejouee)", str(state.qty_scrap))
    tbl.add_row("ops demarrees", str(len(state.operations_started)))
    tbl.add_row("ops terminees", str(len(state.operations_finished)))
    console.print(tbl)

    timeline = Table(title="Timeline event-sourcee")
    timeline.add_column("#", justify="right")
    timeline.add_column("evenement")
    for i, line in enumerate(state.timeline, start=1):
        timeline.add_row(str(i), line)
    console.print(timeline)


@app.command("flow")
def flow_cmd(
    run: str = typer.Option("default", help="Nom du run."),
) -> None:
    """Vue du flux physique par poste : pending / running / done."""
    path = _db_path(run)
    if not path.exists():
        console.print(f"[red]ERR[/red] base introuvable : {path}")
        raise typer.Exit(code=1)

    with db_session(path) as conn:
        views = workstation_view(conn)

    tbl = Table(title="Flux physique - vue par poste")
    tbl.add_column("seq", justify="right")
    tbl.add_column("poste")
    tbl.add_column("label")
    tbl.add_column("pending", justify="right")
    tbl.add_column("running (WIP)", justify="right", style="cyan")
    tbl.add_column("done", justify="right")
    tbl.add_column("OF en cours / a venir", overflow="fold")
    for v in views:
        next_of = [
            f"{op['of_id']}({op['article']}, {op['quantity']:g})"
            for op in (v.running + v.pending)
        ]
        tbl.add_row(
            str(v.sequence_idx),
            v.workstation_id,
            v.label,
            str(len(v.pending)),
            str(v.wip),
            str(len(v.done)),
            ", ".join(next_of) if next_of else "-",
        )
    console.print(tbl)


@app.command("of-detail")
def of_detail_cmd(
    run: str = typer.Option("default", help="Nom du run."),
    of_id: str = typer.Option(..., "--of", help="ID de l'OF a detailler."),
) -> None:
    """Vue detaillee d'un OF : operations, declarations, evenements."""
    path = _db_path(run)
    if not path.exists():
        console.print(f"[red]ERR[/red] base introuvable : {path}")
        raise typer.Exit(code=1)

    with db_session(path) as conn:
        detail = of_detail_view(conn, of_id)

    if detail is None:
        console.print(f"[red]ERR[/red] OF introuvable : {of_id}")
        raise typer.Exit(code=1)

    header = Table(title=f"Detail {detail.of_id}")
    header.add_column("Champ")
    header.add_column("Valeur")
    header.add_row("article", detail.article_id)
    header.add_row("quantite", str(detail.quantity))
    header.add_row("status", detail.status)
    header.add_row("qty_good", str(detail.qty_good))
    header.add_row("qty_scrap", str(detail.qty_scrap))
    console.print(header)

    tbl_ops = Table(title="Operations")
    tbl_ops.add_column("seq", justify="right")
    tbl_ops.add_column("workstation")
    tbl_ops.add_column("status")
    tbl_ops.add_column("qty_good", justify="right")
    tbl_ops.add_column("qty_scrap", justify="right")
    tbl_ops.add_column("actual_start")
    tbl_ops.add_column("actual_end")
    for op in detail.operations:
        tbl_ops.add_row(
            str(op.sequence_idx),
            op.workstation_id,
            op.status,
            str(op.qty_good),
            str(op.qty_scrap),
            op.actual_start or "-",
            op.actual_end or "-",
        )
    console.print(tbl_ops)

    tbl_ev = Table(title=f"Event store ({len(detail.events)} evenements)")
    tbl_ev.add_column("id", justify="right")
    tbl_ev.add_column("at")
    tbl_ev.add_column("type")
    tbl_ev.add_column("payload", overflow="fold")
    for ev in detail.events:
        tbl_ev.add_row(
            str(ev["event_id"]),
            ev["occurred_at"],
            ev["event_type"],
            ev["payload_json"],
        )
    console.print(tbl_ev)


@app.command("summary")
def summary(
    run: str = typer.Option("default", help="Nom du run."),
) -> None:
    """Resume rapide du contenu de la base."""
    path = _db_path(run)
    if not path.exists():
        console.print(f"[red]ERR[/red] base introuvable : {path}")
        raise typer.Exit(code=1)

    queries = [
        ("articles", "SELECT COUNT(*) FROM articles"),
        ("workstations", "SELECT COUNT(*) FROM workstations"),
        ("calendars", "SELECT COUNT(*) FROM calendars"),
        ("bom_lines", "SELECT COUNT(*) FROM bom_lines"),
        ("routing_operations", "SELECT COUNT(*) FROM routing_operations"),
        ("parameters", "SELECT COUNT(*) FROM parameters"),
        ("sales_orders", "SELECT COUNT(*) FROM sales_orders"),
        ("candidate_orders", "SELECT COUNT(*) FROM candidate_orders"),
        ("manufacturing_orders", "SELECT COUNT(*) FROM manufacturing_orders"),
        ("order_operations", "SELECT COUNT(*) FROM order_operations"),
        ("mes_declarations", "SELECT COUNT(*) FROM mes_declarations"),
        ("event_store", "SELECT COUNT(*) FROM event_store"),
        ("gate_decisions", "SELECT COUNT(*) FROM gate_decisions"),
    ]
    tbl = Table(title=f"Resume run {run}")
    tbl.add_column("Table")
    tbl.add_column("Lignes", justify="right")
    with db_session(path) as conn:
        for name, sql in queries:
            count = conn.execute(sql).fetchone()[0]
            tbl.add_row(name, str(count))
    console.print(tbl)


if __name__ == "__main__":
    app()
