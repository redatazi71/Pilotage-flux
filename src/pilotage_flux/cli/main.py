"""Point d'entree CLI : `pflux ...`."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from pilotage_flux.aps import (
    flatten_bom_for_article,
    get_pegging_chain,
    persist_flattened_bom,
)
from pilotage_flux.db import init_schema, db_session
from pilotage_flux.flux import (
    add_candidate_to_contract,
    compute_coherence,
    compute_smoothing,
    create_contract,
    fetch_contract,
    fetch_freeze_batch,
    fetch_version,
    get_batch_contracts,
    get_candidates_in_version,
    get_smoothed_launches,
    list_contracts,
    list_freeze_batches,
    remove_candidate_from_contract,
)
from pilotage_flux.gates import (
    evaluate_p3_for_contract,
    fragment_of,
    get_lineage,
    return_to_negociable,
    run_p2_on_libre_zone,
    run_p3_freeze,
)
from pilotage_flux.stocks_purchasing import (
    cancel_purchase,
    create_purchase,
    list_purchases,
    list_stocks,
    receive_purchase,
    set_stock,
)
from pilotage_flux.quality import (
    create_control,
    declare_control_pass,
    list_controls,
    list_events as quality_list_events,
    open_nc,
    release_of as quality_release_of,
    rework_nc,
    scrap_nc,
)
from pilotage_flux.logistics import (
    create_location,
    feed_workstation,
    list_events as logistic_list_events,
    list_locations,
    queue_at,
    ship,
)
from pilotage_flux.aps import (
    add_alternative,
    list_alternatives_for,
    pick_workstation,
)
from pilotage_flux.risk_debt import (
    expire_overdue_risk_debts,
    extinguish_risk_debt,
    list_risk_debts,
)
from pilotage_flux.rules import load_active_rules
from pilotage_flux.zones import (
    ZONE_GELEE,
    ZONE_LIBRE,
    ZONE_NEGOCIABLE,
    close_cycle,
    create_cycle,
    fetch_in_zone,
    list_cycles,
    move_candidate_to_zone,
    open_cycle,
    transitions_for,
)
from pilotage_flux.events import (
    fetch_events,
    EventType,
    append_event,
    reconstruct_of,
)
from pilotage_flux.gates import run_p1_promotion, run_p3_collective_freeze
from pilotage_flux.importers import import_referentials
from pilotage_flux.mes import (
    compute_consumption_gaps,
    declare_consumption,
    launch_of,
    list_consumptions,
    start_operation,
    finish_operation,
    close_of,
)
from pilotage_flux.visualization import (
    decision_flow_view,
    event_flow_view,
    material_flow_view,
    of_detail_view,
    quality_flow_view,
    workstation_view,
)
from pilotage_flux.costing import (
    compute_of_cost,
    compute_run_cost_report,
    seed_default_unit_costs,
)
from pilotage_flux.comparative import (
    ALL_SCENARIOS,
    DOCTRINES,
    baseline_scenario,
    build_comparative_report,
    build_variance_report,
    compute_kpis,
    run_doctrine,
    run_variance_study,
)


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


@app.command("flux-create")
def flux_create_cmd(
    run: str = typer.Option("default", help="Nom du run."),
    horizon_label: str = typer.Option(..., "--horizon", help="Etiquette horizon (ex: 2026-W27)."),
    horizon_start: str = typer.Option(..., "--start", help="Debut horizon (ISO date)."),
    horizon_end: str = typer.Option(..., "--end", help="Fin horizon (ISO date)."),
    candidates: str = typer.Option(..., "--candidates", help="Liste CSV des candidate_id."),
    notes: str = typer.Option(None, "--notes"),
) -> None:
    """Cree un contrat de flux v1 regroupant des candidates negociables."""
    path = _db_path(run)
    with db_session(path) as conn:
        cand_ids = [c.strip() for c in candidates.split(",") if c.strip()]
        contract = create_contract(
            conn,
            horizon_label=horizon_label,
            horizon_start=horizon_start,
            horizon_end=horizon_end,
            candidate_ids=cand_ids,
            notes=notes,
        )
    console.print(
        f"[green]OK[/green] {contract.contract_id} cree "
        f"({len(cand_ids)} candidates, horizon {horizon_label}, v1)"
    )


@app.command("flux-list")
def flux_list_cmd(
    run: str = typer.Option("default", help="Nom du run."),
    status: str = typer.Option(None, "--status"),
) -> None:
    """Liste les contrats de flux."""
    path = _db_path(run)
    with db_session(path) as conn:
        contracts = list_contracts(conn, status=status)
    if not contracts:
        console.print("[yellow]Aucun contrat.[/yellow]")
        return
    tbl = Table(title=f"Contrats de flux ({len(contracts)})")
    tbl.add_column("id")
    tbl.add_column("horizon")
    tbl.add_column("v courante", justify="right")
    tbl.add_column("status")
    tbl.add_column("created")
    for c in contracts:
        tbl.add_row(
            c.contract_id, c.horizon_label, str(c.current_version),
            c.status, c.created_at,
        )
    console.print(tbl)


@app.command("flux-detail")
def flux_detail_cmd(
    run: str = typer.Option("default", help="Nom du run."),
    contract_id: str = typer.Option(..., "--id"),
    version: int = typer.Option(None, "--version", help="Defaut : version courante."),
) -> None:
    """Detail d'un contrat (entete + version + candidates)."""
    path = _db_path(run)
    with db_session(path) as conn:
        contract = fetch_contract(conn, contract_id)
        if contract is None:
            console.print(f"[red]ERR[/red] contrat inconnu : {contract_id}")
            raise typer.Exit(code=1)
        v = version if version is not None else contract.current_version
        ver = fetch_version(conn, contract_id, v)
        if ver is None:
            console.print(f"[red]ERR[/red] version {v} introuvable")
            raise typer.Exit(code=1)
        cands = get_candidates_in_version(conn, contract_id, v)

    header = Table(title=f"{contract.contract_id} - v{v}")
    header.add_column("champ")
    header.add_column("valeur")
    header.add_row("horizon", f"{contract.horizon_label} ({contract.horizon_start} -> {contract.horizon_end})")
    header.add_row("status", contract.status)
    header.add_row("version courante", str(contract.current_version))
    header.add_row("takt cible (min/piece)", f"{ver.takt_target_min:.2f}" if ver.takt_target_min else "-")
    header.add_row("WIP cible", f"{ver.wip_target:.0f}" if ver.wip_target else "-")
    header.add_row("quantite totale", f"{ver.total_quantity:.0f}")
    header.add_row("coherent", "oui" if ver.is_coherent else "non")
    console.print(header)

    tbl = Table(title=f"Candidates v{v} ({len(cands)})")
    tbl.add_column("seq", justify="right")
    tbl.add_column("candidate")
    tbl.add_column("article")
    tbl.add_column("qty", justify="right")
    tbl.add_column("zone")
    for c in cands:
        tbl.add_row(
            str(c["sequence_idx"]), c["candidate_id"],
            c["article_id"], f"{c['qty_in_contract']:g}", c["zone"],
        )
    console.print(tbl)


@app.command("flux-add")
def flux_add_cmd(
    run: str = typer.Option("default"),
    contract_id: str = typer.Option(..., "--id"),
    candidate_id: str = typer.Option(..., "--candidate"),
    notes: str = typer.Option(None, "--notes"),
) -> None:
    """Ajoute un candidate au contrat -> nouvelle version."""
    path = _db_path(run)
    with db_session(path) as conn:
        new_v = add_candidate_to_contract(conn, contract_id, candidate_id, notes=notes)
    console.print(f"[green]OK[/green] {contract_id} : nouvelle version {new_v}")


@app.command("flux-remove")
def flux_remove_cmd(
    run: str = typer.Option("default"),
    contract_id: str = typer.Option(..., "--id"),
    candidate_id: str = typer.Option(..., "--candidate"),
    notes: str = typer.Option(None, "--notes"),
) -> None:
    """Retire un candidate du contrat -> nouvelle version."""
    path = _db_path(run)
    with db_session(path) as conn:
        new_v = remove_candidate_from_contract(conn, contract_id, candidate_id, notes=notes)
    console.print(f"[green]OK[/green] {contract_id} : nouvelle version {new_v}")


@app.command("flux-check")
def flux_check_cmd(
    run: str = typer.Option("default"),
    contract_id: str = typer.Option(..., "--id"),
    version: int = typer.Option(None, "--version"),
) -> None:
    """Verifie la coherence d'un contrat (charge poste + takt vs goulot)."""
    path = _db_path(run)
    with db_session(path) as conn:
        report = compute_coherence(conn, contract_id, version)

    tbl = Table(
        title=f"Coherence {contract_id} v{report.version} - {'OK' if report.overall_ok else 'VIOLATIONS'}"
    )
    tbl.add_column("metric")
    tbl.add_column("workstation")
    tbl.add_column("actual", justify="right")
    tbl.add_column("limit", justify="right")
    tbl.add_column("ok", justify="center")
    tbl.add_column("explanation", overflow="fold")
    for c in report.checks:
        tbl.add_row(
            c.metric,
            c.workstation_id or "-",
            f"{c.actual_value:.2f}" if c.actual_value is not None else "-",
            f"{c.limit_value:.2f}" if c.limit_value is not None else "-",
            "[green]oui[/green]" if c.is_ok else "[red]non[/red]",
            c.explanation,
        )
    console.print(tbl)


@app.command("flux-smooth")
def flux_smooth_cmd(
    run: str = typer.Option("default"),
    contract_id: str = typer.Option(..., "--id"),
    version: int = typer.Option(None, "--version"),
) -> None:
    """Calcule la distribution lissee des lancements."""
    path = _db_path(run)
    with db_session(path) as conn:
        launches = compute_smoothing(conn, contract_id, version)

    if not launches:
        console.print("[yellow]Aucun lancement lisse.[/yellow]")
        return
    tbl = Table(title=f"Lancements lisses - {contract_id}")
    tbl.add_column("candidate")
    tbl.add_column("offset (min)", justify="right")
    tbl.add_column("planned_start")
    for l in launches:
        tbl.add_row(l.candidate_id, str(l.offset_minutes), l.planned_start)
    console.print(tbl)


@app.command("quality-control-create")
def quality_control_create_cmd(
    run: str = typer.Option("default"),
    article: str = typer.Option(..., "--article"),
    label: str = typer.Option(..., "--label"),
    criterion: str = typer.Option(..., "--criterion"),
    sample_rate: float = typer.Option(1.0, "--sample-rate"),
    blocking: bool = typer.Option(True, "--blocking/--non-blocking"),
) -> None:
    """Cree un plan de controle qualite."""
    path = _db_path(run)
    with db_session(path) as conn:
        c = create_control(
            conn, article_id=article, label=label, criterion=criterion,
            sample_rate=sample_rate, blocking=blocking,
        )
    console.print(
        f"[green]OK[/green] controle {c.control_id} cree : {article} / {label}"
    )


@app.command("quality-control-pass")
def quality_control_pass_cmd(
    run: str = typer.Option("default"),
    of_id: str = typer.Option(..., "--of"),
    control_id: int = typer.Option(..., "--control"),
    qty: float = typer.Option(None, "--qty"),
) -> None:
    """Declare un controle PASS sur un OF."""
    path = _db_path(run)
    with db_session(path) as conn:
        e = declare_control_pass(
            conn, of_id=of_id, control_id=control_id, qty_concerned=qty,
        )
    console.print(f"[green]OK[/green] control_pass {e.quality_event_id} sur {of_id}")


@app.command("quality-nc-open")
def quality_nc_open_cmd(
    run: str = typer.Option("default"),
    of_id: str = typer.Option(..., "--of"),
    qty: float = typer.Option(..., "--qty"),
    explanation: str = typer.Option(None, "--explanation"),
) -> None:
    """Ouvre une non-conformite sur un OF."""
    path = _db_path(run)
    with db_session(path) as conn:
        e = open_nc(
            conn, of_id=of_id, qty_concerned=qty, explanation=explanation,
        )
    console.print(f"[yellow]NC[/yellow] {e.quality_event_id} ouverte sur {of_id} ({qty:g} pcs)")


@app.command("quality-release")
def quality_release_cmd(
    run: str = typer.Option("default"),
    of_id: str = typer.Option(..., "--of"),
    explanation: str = typer.Option(None, "--explanation"),
) -> None:
    """Libere un OF apres validation qualite."""
    path = _db_path(run)
    with db_session(path) as conn:
        e = quality_release_of(conn, of_id=of_id, explanation=explanation)
    console.print(f"[green]RELEASE[/green] {e.quality_event_id} : {of_id} libere")


@app.command("quality-events")
def quality_events_cmd(
    run: str = typer.Option("default"),
    of_id: str = typer.Option(None, "--of"),
) -> None:
    """Liste les evenements qualite (filtrable par OF)."""
    path = _db_path(run)
    with db_session(path) as conn:
        events = quality_list_events(conn, of_id=of_id)
    if not events:
        console.print("[yellow]Aucun evenement qualite.[/yellow]")
        return
    tbl = Table(title=f"Quality events ({len(events)})")
    tbl.add_column("id", justify="right")
    tbl.add_column("of_id")
    tbl.add_column("type")
    tbl.add_column("severity")
    tbl.add_column("qty", justify="right")
    tbl.add_column("at")
    tbl.add_column("explanation", overflow="fold")
    for e in events:
        tbl.add_row(
            str(e.quality_event_id), e.of_id, e.event_type, e.severity,
            f"{e.qty_concerned:g}" if e.qty_concerned else "-",
            e.at_time, e.explanation or "-",
        )
    console.print(tbl)


@app.command("location-create")
def location_create_cmd(
    run: str = typer.Option("default"),
    loc_id: str = typer.Option(..., "--id"),
    label: str = typer.Option(..., "--label"),
    kind: str = typer.Option(..., "--kind", help="stock | ws_in | ws_out | shipping"),
    workstation: str = typer.Option(None, "--ws"),
    capacity: int = typer.Option(None, "--capacity"),
) -> None:
    """Cree un emplacement logistique."""
    path = _db_path(run)
    with db_session(path) as conn:
        loc = create_location(
            conn, location_id=loc_id, label=label, kind=kind,
            workstation_id=workstation, capacity=capacity,
        )
    console.print(f"[green]OK[/green] {loc.location_id} ({loc.kind}) cree")


@app.command("location-list")
def location_list_cmd(
    run: str = typer.Option("default"),
    kind: str = typer.Option(None, "--kind"),
) -> None:
    """Liste les emplacements logistiques."""
    path = _db_path(run)
    with db_session(path) as conn:
        locs = list_locations(conn, kind=kind)
    if not locs:
        console.print("[yellow]Aucun emplacement.[/yellow]")
        return
    tbl = Table(title=f"Emplacements ({len(locs)})")
    tbl.add_column("id")
    tbl.add_column("label")
    tbl.add_column("kind")
    tbl.add_column("workstation")
    tbl.add_column("capacity", justify="right")
    for l in locs:
        tbl.add_row(
            l.location_id, l.label, l.kind,
            l.workstation_id or "-",
            str(l.capacity) if l.capacity else "-",
        )
    console.print(tbl)


@app.command("logistic-feed")
def logistic_feed_cmd(
    run: str = typer.Option("default"),
    of_id: str = typer.Option(..., "--of"),
    article: str = typer.Option(..., "--article"),
    qty: float = typer.Option(..., "--qty"),
    to_location: str = typer.Option(..., "--to"),
) -> None:
    """Alimente un poste (event feed)."""
    path = _db_path(run)
    with db_session(path) as conn:
        e = feed_workstation(
            conn, of_id=of_id, of_op_id=None,
            article_id=article, qty=qty, to_location=to_location,
        )
    console.print(f"[green]OK[/green] feed {e.log_event_id} : {qty:g} {article} -> {to_location}")


@app.command("logistic-ship")
def logistic_ship_cmd(
    run: str = typer.Option("default"),
    of_id: str = typer.Option(..., "--of"),
    article: str = typer.Option(..., "--article"),
    qty: float = typer.Option(..., "--qty"),
    from_location: str = typer.Option(..., "--from"),
) -> None:
    """Expedie un produit fini (event ship)."""
    path = _db_path(run)
    with db_session(path) as conn:
        e = ship(
            conn, of_id=of_id, article_id=article, qty=qty,
            from_location=from_location,
        )
    console.print(f"[green]SHIP[/green] {e.log_event_id} : {qty:g} {article} depuis {from_location}")


@app.command("logistic-events")
def logistic_events_cmd(
    run: str = typer.Option("default"),
    of_id: str = typer.Option(None, "--of"),
) -> None:
    """Liste les evenements logistiques."""
    path = _db_path(run)
    with db_session(path) as conn:
        events = logistic_list_events(conn, of_id=of_id)
    if not events:
        console.print("[yellow]Aucun evenement logistique.[/yellow]")
        return
    tbl = Table(title=f"Logistic events ({len(events)})")
    tbl.add_column("id", justify="right")
    tbl.add_column("of_id")
    tbl.add_column("type")
    tbl.add_column("article")
    tbl.add_column("qty", justify="right")
    tbl.add_column("from")
    tbl.add_column("to")
    for e in events:
        tbl.add_row(
            str(e.log_event_id), e.of_id or "-", e.event_type,
            e.article_id or "-", f"{e.qty:g}",
            e.from_location or "-", e.to_location or "-",
        )
    console.print(tbl)


@app.command("logistic-queue")
def logistic_queue_cmd(
    run: str = typer.Option("default"),
    location: str = typer.Option(..., "--location"),
) -> None:
    """Affiche la file (net = entrees - sorties) a un emplacement."""
    path = _db_path(run)
    with db_session(path) as conn:
        net = queue_at(conn, location)
    console.print(f"File a [bold]{location}[/bold] : {net:g}")


@app.command("routing-alt-add")
def routing_alt_add_cmd(
    run: str = typer.Option("default"),
    article: str = typer.Option(..., "--article"),
    seq: int = typer.Option(..., "--seq"),
    workstation: str = typer.Option(..., "--ws"),
    unit_time: float = typer.Option(..., "--time"),
    preference: int = typer.Option(100, "--pref"),
) -> None:
    """Ajoute une alternative de routing (implantation parallele)."""
    path = _db_path(run)
    with db_session(path) as conn:
        alt = add_alternative(
            conn, article_id=article, sequence_idx=seq,
            workstation_id=workstation, unit_time_min=unit_time,
            preference_order=preference,
        )
    console.print(
        f"[green]OK[/green] alternative {alt.alt_id} : {article}/seq{seq} -> "
        f"{workstation} ({unit_time:g} min, pref {preference})"
    )


@app.command("routing-alt-list")
def routing_alt_list_cmd(
    run: str = typer.Option("default"),
    article: str = typer.Option(..., "--article"),
    seq: int = typer.Option(..., "--seq"),
) -> None:
    """Liste les alternatives + le routing principal pour une operation."""
    from pilotage_flux.aps import available_workstations_for

    path = _db_path(run)
    with db_session(path) as conn:
        choices = available_workstations_for(conn, article, seq)
    if not choices:
        console.print(f"[yellow]Aucun routing pour {article}/seq{seq}.[/yellow]")
        return
    tbl = Table(title=f"Postes disponibles - {article} seq {seq}")
    tbl.add_column("source")
    tbl.add_column("workstation")
    tbl.add_column("unit_time", justify="right")
    tbl.add_column("preference", justify="right")
    for c in choices:
        tbl.add_row(c.source, c.workstation_id, f"{c.unit_time_min:g}",
                    str(c.preference_order))
    console.print(tbl)


@app.command("declare-consumption")
def declare_consumption_cmd(
    run: str = typer.Option("default"),
    of_id: str = typer.Option(..., "--of"),
    article: str = typer.Option(..., "--article"),
    qty: float = typer.Option(..., "--qty"),
    note: str = typer.Option(None, "--note"),
) -> None:
    """Declare une consommation matiere reelle pour un OF."""
    path = _db_path(run)
    with db_session(path) as conn:
        c = declare_consumption(
            conn, of_id=of_id, article_id=article, qty_consumed=qty, note=note
        )
    console.print(
        f"[green]OK[/green] consumption {c.consumption_id} : "
        f"{of_id} consomme {qty:g} {article}"
    )


@app.command("consumption-list")
def consumption_list_cmd(
    run: str = typer.Option("default"),
    of_id: str = typer.Option(None, "--of"),
) -> None:
    """Liste les consommations matiere (filtrable par OF)."""
    path = _db_path(run)
    with db_session(path) as conn:
        cons = list_consumptions(conn, of_id=of_id)
    if not cons:
        console.print("[yellow]Aucune consommation.[/yellow]")
        return
    tbl = Table(title=f"Consommations ({len(cons)})")
    tbl.add_column("id", justify="right")
    tbl.add_column("of_id")
    tbl.add_column("article")
    tbl.add_column("qty", justify="right")
    tbl.add_column("at")
    tbl.add_column("note")
    for c in cons:
        tbl.add_row(
            str(c.consumption_id), c.of_id, c.article_id,
            f"{c.qty_consumed:g}", c.at_time, c.note or "-",
        )
    console.print(tbl)


@app.command("consumption-gaps")
def consumption_gaps_cmd(
    run: str = typer.Option("default"),
    of_id: str = typer.Option(..., "--of"),
) -> None:
    """Affiche les ecarts matiere d'un OF (reel vs theorique BOM)."""
    path = _db_path(run)
    with db_session(path) as conn:
        gaps = compute_consumption_gaps(conn, of_id)
    if not gaps:
        console.print("[yellow]Aucun composant dans la BOM.[/yellow]")
        return
    tbl = Table(title=f"Ecarts consommation {of_id}")
    tbl.add_column("article")
    tbl.add_column("theorique", justify="right")
    tbl.add_column("reel", justify="right")
    tbl.add_column("ecart", justify="right")
    tbl.add_column("ratio", justify="right")
    for g in gaps:
        color = "green" if abs(g.gap) < 0.01 else ("yellow" if g.gap < 0 else "red")
        tbl.add_row(
            g.article_id,
            f"{g.qty_theoretical:g}",
            f"{g.qty_real:g}",
            f"[{color}]{g.gap:+g}[/{color}]",
            f"{g.gap_ratio:+.1%}",
        )
    console.print(tbl)


@app.command("stock-list")
def stock_list_cmd(
    run: str = typer.Option("default"),
) -> None:
    """Liste les niveaux de stock."""
    path = _db_path(run)
    with db_session(path) as conn:
        levels = list_stocks(conn)
    if not levels:
        console.print("[yellow]Aucun stock initialise.[/yellow]")
        return
    tbl = Table(title=f"Stocks ({len(levels)})")
    tbl.add_column("article")
    tbl.add_column("qty_available", justify="right")
    tbl.add_column("qty_reserved", justify="right")
    tbl.add_column("qty_free", justify="right")
    tbl.add_column("updated_at")
    for s in levels:
        tbl.add_row(
            s.article_id,
            f"{s.qty_available:g}",
            f"{s.qty_reserved:g}",
            f"{s.qty_free:g}",
            s.updated_at,
        )
    console.print(tbl)


@app.command("stock-set")
def stock_set_cmd(
    run: str = typer.Option("default"),
    article: str = typer.Option(..., "--article"),
    qty: float = typer.Option(..., "--qty"),
) -> None:
    """Definit le qty_available d'un article (idempotent)."""
    path = _db_path(run)
    with db_session(path) as conn:
        result = set_stock(conn, article, qty)
    console.print(
        f"[green]OK[/green] stock {article} : qty_available={result.qty_available:g}, "
        f"qty_reserved={result.qty_reserved:g}"
    )


@app.command("po-create")
def po_create_cmd(
    run: str = typer.Option("default"),
    article: str = typer.Option(..., "--article"),
    qty: float = typer.Option(..., "--qty"),
    expected_at: str = typer.Option(None, "--expected"),
    supplier: str = typer.Option(None, "--supplier"),
) -> None:
    """Cree un achat ouvert."""
    path = _db_path(run)
    with db_session(path) as conn:
        po = create_purchase(
            conn, article_id=article, qty_ordered=qty,
            expected_at=expected_at, supplier_ref=supplier,
        )
    console.print(
        f"[green]OK[/green] {po.po_id} cree : {article} qty {qty:g} "
        f"(expected {expected_at or '-'})"
    )


@app.command("po-list")
def po_list_cmd(
    run: str = typer.Option("default"),
    status: str = typer.Option(None, "--status"),
    article: str = typer.Option(None, "--article"),
) -> None:
    """Liste les achats ouverts."""
    path = _db_path(run)
    with db_session(path) as conn:
        pos = list_purchases(conn, status=status, article_id=article)
    if not pos:
        console.print("[yellow]Aucun achat.[/yellow]")
        return
    tbl = Table(title=f"Achats ({len(pos)})")
    tbl.add_column("po_id")
    tbl.add_column("article")
    tbl.add_column("qty_ordered", justify="right")
    tbl.add_column("qty_received", justify="right")
    tbl.add_column("status")
    tbl.add_column("expected")
    for p in pos:
        tbl.add_row(
            p.po_id, p.article_id,
            f"{p.qty_ordered:g}", f"{p.qty_received:g}",
            p.status, p.expected_at or "-",
        )
    console.print(tbl)


@app.command("po-receive")
def po_receive_cmd(
    run: str = typer.Option("default"),
    po_id: str = typer.Option(..., "--id"),
    qty: float = typer.Option(..., "--qty"),
) -> None:
    """Reception (totale ou partielle) d'un PO."""
    path = _db_path(run)
    with db_session(path) as conn:
        po = receive_purchase(conn, po_id, qty_received=qty)
    console.print(
        f"[green]OK[/green] {po_id} : {po.qty_received:g}/{po.qty_ordered:g} "
        f"recus, statut {po.status}"
    )


@app.command("mes-launch")
def mes_launch_cmd(
    run: str = typer.Option("default"),
    of_id: str = typer.Option(..., "--of"),
) -> None:
    """Lance un OF (status='created' -> 'launched'). Primitive pour tests/demos."""
    path = _db_path(run)
    with db_session(path) as conn:
        result = launch_of(conn, of_id)
    console.print(
        f"[green]OK[/green] {of_id} -> launched (event {result.event_id})"
    )


@app.command("p3-return")
def p3_return_cmd(
    run: str = typer.Option("default"),
    candidate_id: str = typer.Option(..., "--candidate"),
    reason: str = typer.Option(..., "--reason"),
    cycle: str = typer.Option(None, "--cycle"),
) -> None:
    """P3 inverse Forme A : ramene un candidate gele en zone negociable.

    L'OF associe doit etre en status='created' (non lance). Il est annule.
    """
    path = _db_path(run)
    with db_session(path) as conn:
        result = return_to_negociable(
            conn, candidate_id, reason=reason, cycle_id=cycle
        )
    if result.cancelled_of_id:
        console.print(
            f"[green]OK[/green] {candidate_id} -> negociable "
            f"(OF {result.cancelled_of_id} annule, event {result.event_id})"
        )
    else:
        console.print(
            f"[green]OK[/green] {candidate_id} -> negociable "
            f"(pas d'OF associe, event {result.event_id})"
        )


@app.command("p3-fragment")
def p3_fragment_cmd(
    run: str = typer.Option("default"),
    of_id: str = typer.Option(..., "--of"),
    fragment_qty: float = typer.Option(..., "--qty", help="Quantite a fragmenter."),
    reason: str = typer.Option(..., "--reason"),
    cycle: str = typer.Option(None, "--cycle"),
) -> None:
    """P3 inverse Forme B : fragmente un OF lance/in_progress.

    Cree un nouvel OF FRAGMENT (status='created') avec `fragment_qty` unites.
    L'OF source garde la portion executee. Conservation : source.qty diminue
    de fragment_qty.
    """
    path = _db_path(run)
    with db_session(path) as conn:
        result = fragment_of(
            conn, of_id, fragment_quantity=fragment_qty, reason=reason, cycle_id=cycle
        )
    console.print(
        f"[green]OK[/green] {of_id} fragmente : source garde "
        f"{result.source_quantity_after:.0f}, fragment {result.fragment_of_id} "
        f"prend {result.fragment_quantity:.0f} (event {result.event_id})"
    )


@app.command("lineage")
def lineage_cmd(
    run: str = typer.Option("default"),
    of_id: str = typer.Option(..., "--of"),
) -> None:
    """Affiche la filiation d'un OF (source remontante + fragments descendants)."""
    path = _db_path(run)
    with db_session(path) as conn:
        nodes = get_lineage(conn, of_id)
    if not nodes:
        console.print(f"[yellow]OF inconnu : {of_id}[/yellow]")
        return
    tbl = Table(title=f"Filiation {of_id}")
    tbl.add_column("of_id")
    tbl.add_column("article")
    tbl.add_column("quantity", justify="right")
    tbl.add_column("status")
    tbl.add_column("parent")
    for n in nodes:
        tbl.add_row(
            n.of_id, n.article_id, f"{n.quantity:g}", n.status,
            n.parent_of_id or "-",
        )
    console.print(tbl)


@app.command("p3")
def p3_cmd(
    run: str = typer.Option("default"),
    contract_id: str = typer.Option(..., "--id", help="ID du contrat a freeze."),
    cycle: str = typer.Option(None, "--cycle"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Evaluer sans freezer."),
) -> None:
    """Execute la porte P3 sur un contrat de flux : evaluation + freeze si OK."""
    path = _db_path(run)
    if not path.exists():
        console.print(f"[red]ERR[/red] base introuvable : {path}")
        raise typer.Exit(code=1)

    with db_session(path) as conn:
        if dry_run:
            criteria = evaluate_p3_for_contract(conn, contract_id)
            tbl = Table(title=f"P3 dry-run - {contract_id}")
            tbl.add_column("rule_id")
            tbl.add_column("criterion")
            tbl.add_column("outcome")
            tbl.add_column("explanation", overflow="fold")
            for c in criteria:
                color = "green" if c.outcome == "PASS" else "red"
                tbl.add_row(
                    c.rule_id, c.criterion,
                    f"[{color}]{c.outcome}[/{color}]", c.explanation,
                )
            console.print(tbl)
            return

        result = run_p3_freeze(conn, contract_id, cycle_id=cycle)

    tbl = Table(title=f"P3 - {contract_id} : {result.decision}")
    tbl.add_column("rule_id")
    tbl.add_column("criterion")
    tbl.add_column("outcome")
    tbl.add_column("explanation", overflow="fold")
    for c in result.criteria:
        color = "green" if c.outcome == "PASS" else "red"
        tbl.add_row(c.rule_id, c.criterion, f"[{color}]{c.outcome}[/{color}]", c.explanation)
    console.print(tbl)
    if result.batch_id:
        console.print(f"[green]FREEZE OK[/green] tranche {result.batch_id} creee.")


@app.command("freeze-list")
def freeze_list_cmd(
    run: str = typer.Option("default"),
    status: str = typer.Option(None, "--status"),
) -> None:
    """Liste les tranches gelees."""
    path = _db_path(run)
    with db_session(path) as conn:
        batches = list_freeze_batches(conn, status=status)
    if not batches:
        console.print("[yellow]Aucune tranche gelee.[/yellow]")
        return
    tbl = Table(title=f"Tranches gelees ({len(batches)})")
    tbl.add_column("batch_id")
    tbl.add_column("horizon")
    tbl.add_column("decision")
    tbl.add_column("contracts", justify="right")
    tbl.add_column("candidates", justify="right")
    tbl.add_column("qty totale", justify="right")
    tbl.add_column("status")
    tbl.add_column("frozen_at")
    for b in batches:
        tbl.add_row(
            b.batch_id,
            f"{b.horizon_start} -> {b.horizon_end}",
            b.decision,
            str(b.contract_count),
            str(b.candidate_count),
            f"{b.total_quantity:.0f}",
            b.status,
            b.frozen_at,
        )
    console.print(tbl)


@app.command("freeze-detail")
def freeze_detail_cmd(
    run: str = typer.Option("default"),
    batch_id: str = typer.Option(..., "--id"),
) -> None:
    """Detail d'une tranche gelee : entete + contrats figes."""
    path = _db_path(run)
    with db_session(path) as conn:
        batch = fetch_freeze_batch(conn, batch_id)
        if batch is None:
            console.print(f"[red]ERR[/red] tranche inconnue : {batch_id}")
            raise typer.Exit(code=1)
        contracts = get_batch_contracts(conn, batch_id)

    header = Table(title=f"Tranche gelee {batch_id}")
    header.add_column("champ")
    header.add_column("valeur")
    header.add_row("horizon", f"{batch.horizon_start} -> {batch.horizon_end}")
    header.add_row("decision", batch.decision)
    header.add_row("status", batch.status)
    header.add_row("contracts", str(batch.contract_count))
    header.add_row("candidates", str(batch.candidate_count))
    header.add_row("quantite totale", f"{batch.total_quantity:.0f}")
    header.add_row("frozen_at", batch.frozen_at)
    header.add_row("explanation", batch.explanation or "-")
    console.print(header)

    tbl = Table(title="Contrats figes")
    tbl.add_column("contract_id")
    tbl.add_column("version (figee)", justify="right")
    for c in contracts:
        tbl.add_row(c.contract_id, str(c.version))
    console.print(tbl)


@app.command("p2")
def p2_cmd(
    run: str = typer.Option("default", help="Nom du run."),
    cycle: str = typer.Option(None, "--cycle", help="Cycle territorial associe (optionnel)."),
) -> None:
    """Execute la porte P2 sur tous les candidates en zone 'libre'.

    Pour chaque candidate, evalue les 5 criteres P2 (regles data-driven en
    table decision_rules) et applique la decision PASS / PASS_WITH_RISK /
    RECALCULATE / BLOCK. Cree une risk_debt par critere en RISK.
    """
    path = _db_path(run)
    if not path.exists():
        console.print(f"[red]ERR[/red] base introuvable : {path}")
        raise typer.Exit(code=1)

    with db_session(path) as conn:
        batch = run_p2_on_libre_zone(conn, cycle_id=cycle)

    summary = Table(title=f"Porte P2 - synthese ({len(batch.results)} candidates evalues)")
    summary.add_column("decision")
    summary.add_column("nb", justify="right")
    summary.add_row("PASS", str(batch.passed))
    summary.add_row("PASS_WITH_RISK", str(batch.passed_with_risk))
    summary.add_row("RECALCULATE", str(batch.recalc))
    summary.add_row("BLOCK", str(batch.blocked))
    summary.add_row("[bold]risk_debts ouvertes[/bold]", str(batch.total_risk_debts))
    console.print(summary)

    tbl = Table(title="Detail par candidate")
    tbl.add_column("candidate")
    tbl.add_column("decision")
    tbl.add_column("transitioned", justify="center")
    tbl.add_column("risk_debts", justify="right")
    tbl.add_column("criteres", overflow="fold")
    for r in batch.results:
        per_rule = ", ".join(f"{rr.rule_id}={rr.outcome}" for rr in r.rule_results)
        tbl.add_row(
            r.candidate_id,
            r.decision,
            "oui" if r.transitioned else "non",
            str(len(r.risk_debts)),
            per_rule,
        )
    console.print(tbl)


@app.command("risk-debt")
def risk_debt_cmd(
    run: str = typer.Option("default", help="Nom du run."),
    status: str = typer.Option(None, "--status", help="Filtrer : open | extinct | expired."),
) -> None:
    """Liste les risk_debts."""
    path = _db_path(run)
    if not path.exists():
        console.print(f"[red]ERR[/red] base introuvable : {path}")
        raise typer.Exit(code=1)
    with db_session(path) as conn:
        debts = list_risk_debts(conn, status=status)

    if not debts:
        console.print("[yellow]Aucune risk_debt.[/yellow]")
        return

    tbl = Table(title=f"Risk debts ({len(debts)})")
    tbl.add_column("id", justify="right")
    tbl.add_column("candidate")
    tbl.add_column("rule")
    tbl.add_column("score", justify="right")
    tbl.add_column("deadline")
    tbl.add_column("status")
    tbl.add_column("explanation", overflow="fold")
    for d in debts:
        tbl.add_row(
            str(d.risk_debt_id),
            d.candidate_id,
            d.rule_id,
            f"{d.score:.2f}",
            d.deadline,
            d.status,
            d.explanation or "-",
        )
    console.print(tbl)


@app.command("extinguish-debt")
def extinguish_debt_cmd(
    run: str = typer.Option("default", help="Nom du run."),
    debt_id: int = typer.Option(..., "--id", help="ID de la risk_debt."),
    reason: str = typer.Option(..., "--reason", help="Raison d'extinction."),
) -> None:
    """Eteint manuellement une risk_debt (open -> extinct)."""
    path = _db_path(run)
    with db_session(path) as conn:
        d = extinguish_risk_debt(conn, debt_id, reason=reason)
    console.print(
        f"[green]OK[/green] risk_debt {d.risk_debt_id} eteinte "
        f"({d.candidate_id}, raison : {reason})"
    )


@app.command("expire-debts")
def expire_debts_cmd(
    run: str = typer.Option("default", help="Nom du run."),
) -> None:
    """Passe les risk_debts overdue en statut 'expired'."""
    path = _db_path(run)
    with db_session(path) as conn:
        n = expire_overdue_risk_debts(conn)
    console.print(f"[green]OK[/green] {n} risk_debt(s) expirees.")


@app.command("rules")
def rules_cmd(
    run: str = typer.Option("default", help="Nom du run."),
    gate: str = typer.Option("P2", "--gate", help="Porte (P2/P3/P4)."),
) -> None:
    """Liste les regles actives pour une porte."""
    path = _db_path(run)
    if not path.exists():
        console.print(f"[red]ERR[/red] base introuvable : {path}")
        raise typer.Exit(code=1)
    with db_session(path) as conn:
        rules = load_active_rules(conn, gate)

    if not rules:
        console.print(f"[yellow]Aucune regle active pour {gate}.[/yellow]")
        return

    tbl = Table(title=f"Regles actives - {gate} ({len(rules)})")
    tbl.add_column("rule_id")
    tbl.add_column("version", justify="right")
    tbl.add_column("criterion")
    tbl.add_column("severity")
    tbl.add_column("label", overflow="fold")
    for r in rules:
        tbl.add_row(r.rule_id, str(r.version), r.criterion, r.severity, r.label)
    console.print(tbl)


@app.command("zones")
def zones_cmd(
    run: str = typer.Option("default", help="Nom du run."),
) -> None:
    """Vue des candidate_orders par zone (libre / negociable / gelee)."""
    path = _db_path(run)
    if not path.exists():
        console.print(f"[red]ERR[/red] base introuvable : {path}")
        raise typer.Exit(code=1)

    with db_session(path) as conn:
        by_zone = {z: fetch_in_zone(conn, z) for z in (ZONE_LIBRE, ZONE_NEGOCIABLE, ZONE_GELEE)}

    tbl = Table(title="Zones de planification - synthèse")
    tbl.add_column("zone")
    tbl.add_column("nb candidats", justify="right")
    tbl.add_column("candidats", overflow="fold")
    for zone in (ZONE_LIBRE, ZONE_NEGOCIABLE, ZONE_GELEE):
        rows = by_zone[zone]
        listing = ", ".join(
            f"{r['candidate_id']}({r['article_id']}, {r['quantity']:g})"
            for r in rows
        )
        tbl.add_row(zone, str(len(rows)), listing or "-")
    console.print(tbl)


@app.command("move-zone")
def move_zone_cmd(
    run: str = typer.Option("default", help="Nom du run."),
    candidate_id: str = typer.Option(..., "--candidate", help="ID du candidate à déplacer."),
    target: str = typer.Option(..., "--to", help="Zone cible (libre|negociable|gelee)."),
    decision: str = typer.Option(None, "--decision", help="Décision portée (ex: PASS)."),
    actor: str = typer.Option("cli", "--actor"),
    explanation: str = typer.Option(None, "--explanation"),
) -> None:
    """Déplace manuellement un candidate vers une zone (debug / V1 transitionnel)."""
    path = _db_path(run)
    if not path.exists():
        console.print(f"[red]ERR[/red] base introuvable : {path}")
        raise typer.Exit(code=1)

    with db_session(path) as conn:
        t = move_candidate_to_zone(
            conn,
            candidate_id,
            target,
            decision=decision,
            explanation=explanation,
            actor=actor,
        )
    console.print(
        f"[green]OK[/green] {candidate_id} : {t.from_zone} -> {t.to_zone} "
        f"(transition {t.transition_id})"
    )


@app.command("transitions")
def transitions_cmd(
    run: str = typer.Option("default", help="Nom du run."),
    candidate_id: str = typer.Option(..., "--candidate"),
) -> None:
    """Historique des transitions de zone d'un candidate."""
    path = _db_path(run)
    if not path.exists():
        console.print(f"[red]ERR[/red] base introuvable : {path}")
        raise typer.Exit(code=1)

    with db_session(path) as conn:
        history = transitions_for(conn, candidate_id)

    if not history:
        console.print(f"[yellow]Aucune transition pour {candidate_id}[/yellow]")
        return

    tbl = Table(title=f"Transitions {candidate_id}")
    tbl.add_column("id", justify="right")
    tbl.add_column("from")
    tbl.add_column("->", justify="center")
    tbl.add_column("to")
    tbl.add_column("decision")
    tbl.add_column("actor")
    tbl.add_column("at")
    for t in history:
        tbl.add_row(
            str(t.transition_id),
            t.from_zone or "(création)",
            "->",
            t.to_zone,
            t.decision or "-",
            t.actor or "-",
            t.at_time,
        )
    console.print(tbl)


@app.command("cycle-create")
def cycle_create_cmd(
    run: str = typer.Option("default", help="Nom du run."),
    gate: str = typer.Option(..., "--gate", help="Porte (P2 ou P3)."),
    cycle_id: str = typer.Option(..., "--id", help="Identifiant cycle (ex: P2-2026-07)."),
    period_start: str = typer.Option(..., "--start", help="Début ISO (YYYY-MM-DD)."),
    period_end: str = typer.Option(..., "--end", help="Fin ISO (YYYY-MM-DD)."),
    cadence_days: int = typer.Option(None, "--cadence", help="Cadence jours (defaut data-driven)."),
) -> None:
    """Crée un cycle territorial en statut 'planned'."""
    path = _db_path(run)
    if not path.exists():
        console.print(f"[red]ERR[/red] base introuvable : {path}")
        raise typer.Exit(code=1)

    with db_session(path) as conn:
        c = create_cycle(
            conn,
            gate=gate,
            cycle_id=cycle_id,
            period_start=period_start,
            period_end=period_end,
            cadence_days=cadence_days,
        )
    console.print(
        f"[green]OK[/green] cycle {c.cycle_id} créé "
        f"({c.gate}, {c.period_start} -> {c.period_end}, cadence {c.cadence_days}j, statut {c.status})"
    )


@app.command("cycle-open")
def cycle_open_cmd(
    run: str = typer.Option("default"),
    cycle_id: str = typer.Option(..., "--id"),
) -> None:
    """Passe un cycle 'planned' à 'open'."""
    path = _db_path(run)
    with db_session(path) as conn:
        c = open_cycle(conn, cycle_id)
    console.print(f"[green]OK[/green] cycle {c.cycle_id} ouvert à {c.opened_at}")


@app.command("cycle-close")
def cycle_close_cmd(
    run: str = typer.Option("default"),
    cycle_id: str = typer.Option(..., "--id"),
) -> None:
    """Passe un cycle 'open' à 'closed'."""
    path = _db_path(run)
    with db_session(path) as conn:
        c = close_cycle(conn, cycle_id)
    console.print(f"[green]OK[/green] cycle {c.cycle_id} clôturé à {c.closed_at}")


@app.command("cycle-list")
def cycle_list_cmd(
    run: str = typer.Option("default"),
    gate: str = typer.Option(None, "--gate", help="Filtrer par porte (P2/P3)."),
    status: str = typer.Option(None, "--status", help="Filtrer par statut."),
) -> None:
    """Liste les cycles territoriaux."""
    path = _db_path(run)
    if not path.exists():
        console.print(f"[red]ERR[/red] base introuvable : {path}")
        raise typer.Exit(code=1)
    with db_session(path) as conn:
        cycles = list_cycles(conn, gate=gate, status=status)

    if not cycles:
        console.print("[yellow]Aucun cycle.[/yellow]")
        return

    tbl = Table(title="Cycles territoriaux")
    tbl.add_column("cycle_id")
    tbl.add_column("gate")
    tbl.add_column("période")
    tbl.add_column("cadence", justify="right")
    tbl.add_column("statut")
    tbl.add_column("opened_at")
    tbl.add_column("closed_at")
    for c in cycles:
        tbl.add_row(
            c.cycle_id,
            c.gate,
            f"{c.period_start} -> {c.period_end}",
            f"{c.cadence_days}j",
            c.status,
            c.opened_at or "-",
            c.closed_at or "-",
        )
    console.print(tbl)


@app.command("flatten-bom")
def flatten_bom_cmd(
    run: str = typer.Option("default", help="Nom du run."),
    article: str = typer.Option(
        None, "--article", help="Article racine (defaut : tous les articles fabriques)."
    ),
) -> None:
    """Aplatit les nomenclatures multi-niveau et persiste le resultat."""
    path = _db_path(run)
    if not path.exists():
        console.print(f"[red]ERR[/red] base introuvable : {path}")
        raise typer.Exit(code=1)

    with db_session(path) as conn:
        if article is None:
            n = persist_flattened_bom(conn)
            console.print(
                f"[green]OK[/green] {n} lignes inserees dans flattened_bom_lines."
            )
            rows = conn.execute(
                """
                SELECT root_article, component_article, cumulative_quantity,
                       depth_level, is_leaf, path
                FROM flattened_bom_lines
                ORDER BY root_article, depth_level, component_article
                """
            ).fetchall()
        else:
            nodes = flatten_bom_for_article(conn, article)
            rows = [
                {
                    "root_article": article,
                    "component_article": n.component_article,
                    "cumulative_quantity": n.cumulative_quantity,
                    "depth_level": n.depth_level,
                    "is_leaf": 1 if n.is_leaf else 0,
                    "path": n.path,
                }
                for n in nodes
            ]

    if not rows:
        console.print("[yellow]Aucune ligne a afficher.[/yellow]")
        return

    tbl = Table(title=f"Aplatissement BOM ({'tous' if article is None else article})")
    tbl.add_column("racine")
    tbl.add_column("composant")
    tbl.add_column("qte/unite", justify="right")
    tbl.add_column("profondeur", justify="right")
    tbl.add_column("feuille", justify="center")
    tbl.add_column("path")
    for r in rows:
        is_leaf = bool(r["is_leaf"]) if not isinstance(r, dict) else bool(r["is_leaf"])
        tbl.add_row(
            r["root_article"],
            r["component_article"],
            f"{float(r['cumulative_quantity']):g}",
            str(r["depth_level"]),
            "oui" if is_leaf else "non",
            r["path"],
        )
    console.print(tbl)


@app.command("pegging")
def pegging_cmd(
    run: str = typer.Option("default", help="Nom du run."),
    sales_order: str = typer.Option(..., "--so", help="ID du sales_order (ex: SO-001)."),
) -> None:
    """Affiche la chaine de pegging issue d'un sales_order."""
    path = _db_path(run)
    if not path.exists():
        console.print(f"[red]ERR[/red] base introuvable : {path}")
        raise typer.Exit(code=1)

    with db_session(path) as conn:
        chain = get_pegging_chain(conn, "sales_order", sales_order)

    if not chain:
        console.print(f"[yellow]Aucun pegging pour {sales_order}[/yellow]")
        return

    tbl = Table(title=f"Pegging chain - {sales_order}")
    tbl.add_column("depth", justify="right")
    tbl.add_column("source")
    tbl.add_column("->", justify="center")
    tbl.add_column("target")
    tbl.add_column("article")
    tbl.add_column("qte", justify="right")
    for link in chain:
        tbl.add_row(
            str(link.depth),
            f"{link.source_type}/{link.source_id}",
            "->",
            f"{link.target_type}/{link.target_id}",
            link.article_id or "-",
            f"{link.quantity:g}",
        )
    console.print(tbl)


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


@app.command("compare-doctrines")
def compare_doctrines(
    run: str = typer.Option("compare", help="Préfixe des bases SQLite générées."),
    fixtures: Path = typer.Option(
        Path("data/fixtures_v1"),
        help="Fixtures référentiels (V1 multi-niveau requises).",
    ),
    report_path: Path = typer.Option(
        Path("data/comparative_baseline_report.md"),
        help="Chemin de sortie du rapport Markdown.",
    ),
) -> None:
    """Exécute le scénario baseline sur les 3 doctrines (OF, FLUX, EVENT) et publie le rapport L4.3."""
    scenario = baseline_scenario()
    DEFAULT_RUNS_DIR.mkdir(parents=True, exist_ok=True)

    kpis = []
    results = []
    for doctrine in DOCTRINES:
        db = DEFAULT_RUNS_DIR / f"{run}_{doctrine}.db"
        console.print(f"[yellow]→[/yellow] exécution doctrine [bold]{doctrine}[/bold] …")
        result = run_doctrine(scenario, doctrine, db, fixtures_dir=fixtures)
        kpi = compute_kpis(scenario, result)
        kpis.append(kpi)
        results.append(result)
        console.print(
            f"  OFs {kpi.of_closed}/{kpi.of_total} • lead={kpi.lead_time_days_avg}j • "
            f"WIP={kpi.wip_avg} • APS={kpi.aps_recalculations} • "
            f"actions={kpi.actions_triggered}"
        )

    tbl = Table(title=f"Étude comparative V4 — scénario {scenario.name}")
    tbl.add_column("KPI")
    for d in DOCTRINES:
        tbl.add_column(d.upper())
    rows = [
        ("Lead time moyen (j)", "lead_time_days_avg"),
        ("Lead time max (j)", "lead_time_days_max"),
        ("WIP moyen", "wip_avg"),
        ("OF clôturés", "of_closed"),
        ("Recalculs APS", "aps_recalculations"),
        ("Nervosité", "nervousness"),
        ("Écarts détectés", "deviations_detected"),
        ("Actions tolérance", "actions_triggered"),
        ("Replans locaux", "replan_local_actions"),
        ("Replans globaux", "replan_global_actions"),
        ("Causes attachées", "causes_attached"),
        ("Évts qualité", "quality_events"),
    ]
    by_d = {k.doctrine: k for k in kpis}
    for label, field_name in rows:
        cells = [label]
        for d in DOCTRINES:
            v = getattr(by_d[d], field_name)
            cells.append("—" if v is None else str(v))
        tbl.add_row(*cells)
    console.print(tbl)

    report = build_comparative_report(scenario, kpis)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    console.print(f"[green]OK[/green] rapport écrit : [bold]{report_path}[/bold]")


@app.command("costs")
def costs(
    run: str = typer.Option("default", help="Nom du run."),
    seed_defaults: bool = typer.Option(
        True, "--seed/--no-seed",
        help="Seed des prix unitaires/taux horaires si absents (idempotent).",
    ),
    of_id: str | None = typer.Option(None, help="Détail d'un OF spécifique."),
) -> None:
    """Affiche le breakdown coûts (matière + MOD + MOI + scrap) du run."""
    path = _db_path(run)
    if not path.exists():
        console.print(f"[red]ERR[/red] base introuvable : {path}")
        raise typer.Exit(code=1)

    with db_session(path) as conn:
        if seed_defaults:
            n = seed_default_unit_costs(conn)
            if n > 0:
                console.print(f"[dim](seedé {n} paramètres de coût)[/dim]")
        if of_id:
            breakdown = compute_of_cost(conn, of_id)
            console.print(f"[bold]{breakdown.of_id}[/bold] — {breakdown.article_id}")
            console.print(f"  quantité       : {breakdown.quantity:.0f} (bon {breakdown.qty_good:.0f}, rebut {breakdown.qty_scrap:.0f})")
            console.print(f"  matière        : {breakdown.material_cost:.2f} €")
            console.print(f"  MOD            : {breakdown.mod_cost:.2f} €")
            console.print(f"  MOI            : {breakdown.moi_cost:.2f} €")
            console.print(f"  scrap          : {breakdown.scrap_cost:.2f} €")
            console.print(f"  [bold]total          : {breakdown.total_cost:.2f} €[/bold]")
            console.print(f"  coût/unité bon : {breakdown.cost_per_good_unit:.2f} €")
            if breakdown.unvalued_articles:
                console.print(
                    f"  [yellow]articles sans prix[/yellow] : {breakdown.unvalued_articles}"
                )
            if breakdown.unvalued_workstations:
                console.print(
                    f"  [yellow]postes sans taux[/yellow] : {breakdown.unvalued_workstations}"
                )
            return
        report = compute_run_cost_report(conn)

    tbl = Table(title=f"Coûts par OF — run {run}")
    for col in ("OF", "Article", "Qté", "Matière", "MOD", "MOI", "Scrap", "Total", "€/unité bon"):
        tbl.add_column(col, justify="right" if col != "Article" and col != "OF" else "left")
    for b in report.of_breakdowns:
        tbl.add_row(
            b.of_id, b.article_id, f"{b.quantity:.0f}",
            f"{b.material_cost:.0f}", f"{b.mod_cost:.0f}",
            f"{b.moi_cost:.0f}", f"{b.scrap_cost:.0f}",
            f"{b.total_cost:.0f}", f"{b.cost_per_good_unit:.2f}",
        )
    console.print(tbl)
    console.print(
        f"\n[bold]Totaux[/bold] : matière {report.total_material:.0f} € | "
        f"MOD {report.total_mod:.0f} € | MOI {report.total_moi:.0f} € | "
        f"scrap {report.total_scrap:.0f} € | "
        f"[bold]grand total {report.grand_total:.0f} €[/bold] "
        f"({report.cost_per_of:.0f} €/OF)"
    )


@app.command("p3-collective")
def p3_collective(
    run: str = typer.Option("default", help="Nom du run."),
    contracts: str = typer.Option(
        ..., help="IDs de contrats séparés par virgule (ex : FX-0001,FX-0002)."
    ),
    cycle_id: str | None = typer.Option(None, help="ID du cycle territorial (optionnel)."),
) -> None:
    """Évalue et fige collectivement N contrats sur le même horizon (L6.1)."""
    path = _db_path(run)
    if not path.exists():
        console.print(f"[red]ERR[/red] base introuvable : {path}")
        raise typer.Exit(code=1)
    cids = [c.strip() for c in contracts.split(",") if c.strip()]
    with db_session(path) as conn:
        result = run_p3_collective_freeze(conn, cids, cycle_id=cycle_id)
    console.print(
        f"[bold]P3 collective[/bold] horizon {result.horizon_start} → {result.horizon_end}"
    )
    console.print(f"  décision : [yellow]{result.decision}[/yellow]")
    if result.bottleneck_workstation:
        console.print(
            f"  goulot : {result.bottleneck_workstation} "
            f"(charge {result.bottleneck_load:.0f} / capa {result.bottleneck_capacity:.0f} min)"
        )
    if result.frozen_contracts:
        console.print(f"  [green]gelés[/green] : {result.frozen_contracts}")
    if result.deferred_contracts:
        console.print(f"  [yellow]reportés[/yellow] : {result.deferred_contracts}")
    if result.rejected_contracts:
        for cid, reason in result.rejected_contracts:
            console.print(f"  [red]rejeté[/red] {cid} : {reason}")
    if result.batch_id:
        console.print(f"  tranche : [bold]{result.batch_id}[/bold]")


@app.command("flow-material")
def flow_material(
    run: str = typer.Option("default", help="Nom du run."),
) -> None:
    """Famille 2 — flux matière (stocks + PO + conso vs théorique)."""
    path = _db_path(run)
    with db_session(path) as conn:
        report = material_flow_view(conn)
    tbl = Table(title="Flux matière")
    for col in ("Article", "Stock", "Réservé", "PO ouv.", "Consommé",
                "Théorique BOM", "Écart"):
        tbl.add_column(col, justify="right" if col != "Article" else "left")
    for item in report.items:
        tbl.add_row(
            item.article_id,
            f"{item.qty_on_hand:.1f}",
            f"{item.qty_reserved:.1f}",
            f"{item.qty_on_order:.1f}",
            f"{item.qty_consumed:.1f}",
            f"{item.qty_theoretical:.1f}",
            f"{item.qty_gap:+.1f}",
        )
    console.print(tbl)


@app.command("flow-quality")
def flow_quality(
    run: str = typer.Option("default", help="Nom du run."),
) -> None:
    """Famille 3 — flux qualité (yield rate, NCs)."""
    path = _db_path(run)
    with db_session(path) as conn:
        report = quality_flow_view(conn)
    tbl = Table(title=f"Flux qualité (yield global : {report.overall_yield_rate:.1%})")
    for col in ("OF", "Article", "Qté", "Bon", "Rebut", "Yield",
                "NC", "Bloc.", "Libéré"):
        tbl.add_column(col)
    for item in report.items:
        tbl.add_row(
            item.of_id, item.article_id,
            f"{item.quantity:.0f}",
            f"{item.qty_good:.0f}", f"{item.qty_scrap:.0f}",
            f"{item.yield_rate:.1%}",
            str(item.nc_opened),
            "OUI" if item.blocked else "—",
            "OUI" if item.released else "—",
        )
    console.print(tbl)


@app.command("flow-decision")
def flow_decision(
    run: str = typer.Option("default", help="Nom du run."),
) -> None:
    """Famille 4 — flux décisionnel (portes + zones + filtre dual)."""
    path = _db_path(run)
    with db_session(path) as conn:
        report = decision_flow_view(conn)
    tbl_gates = Table(title=f"Décisions des portes ({len(report.gate_decisions)})")
    for col in ("Porte", "Sujet", "Décision", "Cycle", "Quand"):
        tbl_gates.add_column(col)
    for d in report.gate_decisions[-15:]:
        tbl_gates.add_row(
            d.gate, f"{d.subject_type}:{d.subject_id}", d.decision,
            d.cycle_id or "—", d.at_time,
        )
    console.print(tbl_gates)
    tbl_tol = Table(title=f"Filtre dual de tolérances ({len(report.tolerance_actions)})")
    for col in ("ID", "Candidate", "Action", "Score", "Triggered"):
        tbl_tol.add_column(col)
    by_level = report.actions_by_level()
    for a in report.tolerance_actions[-15:]:
        tbl_tol.add_row(
            str(a.decision_id), a.candidate_id or "—",
            a.action_level, f"{a.score_combined:.3f}",
            a.triggered_at or "(pending)",
        )
    console.print(tbl_tol)
    if by_level:
        console.print(f"Niveaux d'action : {by_level}")


@app.command("flow-events")
def flow_events(
    run: str = typer.Option("default", help="Nom du run."),
    batch: str | None = typer.Option(None, help="Limiter à une tranche gelée."),
) -> None:
    """Famille 5 — flux événementiel (attendus vs réels + causes)."""
    path = _db_path(run)
    with db_session(path) as conn:
        report = event_flow_view(conn, batch_id=batch)
    tbl = Table(
        title=f"Flux événementiel — {report.total_matched}/{report.total_expected} "
              f"matched ({report.match_rate:.0%})"
    )
    for col in ("Candidate", "Type", "Attendu", "Réel", "Δ min",
                "Qualif.", "Absorbé", "Cause"):
        tbl.add_column(col)
    for line in report.lines[:30]:
        tbl.add_row(
            line.candidate_id, line.event_type,
            line.expected_at[:16] if line.expected_at else "—",
            line.actual_at[:16] if line.actual_at else "—",
            f"{line.delta_minutes:.0f}" if line.delta_minutes is not None else "—",
            line.qualification or "—",
            "OUI" if line.is_absorbed else "—",
            line.cause_label or "—",
        )
    console.print(tbl)


@app.command("compare-doctrines-extended")
def compare_doctrines_extended(
    seeds: str = typer.Option(
        "42,100,200,300,400",
        help="Liste de seeds (virgule séparée) pour la variance.",
    ),
    scenarios: str = typer.Option(
        "baseline,stress_double_breakdown,stress_cascade_nc,stress_demand_spike",
        help="Liste de scénarios (virgule séparée).",
    ),
    work_dir: Path = typer.Option(
        Path("data/runs/variance"),
        help="Dossier des bases SQLite par run.",
    ),
    fixtures: Path = typer.Option(
        Path("data/fixtures_v1"), help="Fixtures référentiels (V1)."
    ),
    report_path: Path = typer.Option(
        Path("data/comparative_extended_report.md"),
        help="Chemin de sortie du rapport étendu.",
    ),
) -> None:
    """Étude comparative étendue : N scénarios × 3 doctrines × M seeds."""
    seed_list = [int(s) for s in seeds.split(",") if s.strip()]
    scen_list = [s.strip() for s in scenarios.split(",") if s.strip()]
    for s in scen_list:
        if s not in ALL_SCENARIOS:
            console.print(f"[red]ERR[/red] scénario inconnu : {s}")
            raise typer.Exit(code=1)
    total = len(scen_list) * len(DOCTRINES) * len(seed_list)
    console.print(
        f"[yellow]→[/yellow] étude étendue : {len(scen_list)} scénarios × "
        f"{len(DOCTRINES)} doctrines × {len(seed_list)} seeds = [bold]{total}[/bold] runs"
    )
    study = run_variance_study(
        scenarios=scen_list,
        doctrines=list(DOCTRINES),
        seeds=seed_list,
        work_dir=work_dir,
        fixtures_dir=fixtures,
    )
    report = build_variance_report(study)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    console.print(f"[green]OK[/green] rapport étendu : [bold]{report_path}[/bold]")

    # Tableau de résumé par scénario
    tbl = Table(title="Synthèse — apport V3 vs FLUX par scénario")
    tbl.add_column("Scénario")
    tbl.add_column("Δ nervosité", justify="right")
    tbl.add_column("Δ lead time (j)", justify="right")
    tbl.add_column("Δ WIP", justify="right")
    tbl.add_column("Δ coût (€)", justify="right")
    tbl.add_column("Détections V3", justify="right")
    for scen, by_doc in study.aggregates.items():
        if "event" not in by_doc or "flux" not in by_doc:
            continue
        ev = by_doc["event"]
        fx = by_doc["flux"]
        tbl.add_row(
            scen,
            f"{ev.nervousness_mean - fx.nervousness_mean:+.3f}",
            f"{ev.lead_time_avg_mean - fx.lead_time_avg_mean:+.3f}",
            f"{ev.wip_mean - fx.wip_mean:+.3f}",
            f"{ev.total_cost_eur_mean - fx.total_cost_eur_mean:+.0f}",
            f"{ev.deviations_detected_mean:.1f}",
        )
    console.print(tbl)


if __name__ == "__main__":
    app()
