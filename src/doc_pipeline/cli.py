"""Typer CLI for the contract pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console

from doc_pipeline.config import load_config
from doc_pipeline.models import Inventory
from doc_pipeline.utils.progress import (
    console,
    print_client_table,
    print_flagged_clients,
    print_inventory_summary,
)

app = typer.Typer(
    name="pipeline",
    help="Contract price adjustment pipeline for Croatian legal documents.",
    no_args_is_help=True,
)


@app.command()
def setup(
    source: Annotated[
        Optional[Path],
        typer.Option("--source", "-s", help="Source contracts folder path."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Overwrite existing working copy."),
    ] = False,
    scan_only: Annotated[
        bool,
        typer.Option("--scan-only", help="Skip copy, re-scan existing data/source/."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would happen without changes."),
    ] = False,
) -> None:
    """Phase 0: Copy source contracts, scan, classify, and build inventory."""
    from doc_pipeline.phases.setup import run_setup

    config = load_config()
    source_path = source or config.source_path
    run_setup(config, source=source_path, force=force, scan_only=scan_only, dry_run=dry_run)


@app.command()
def extract(
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Re-extract even if JSON already exists."),
    ] = False,
    clients: Annotated[
        Optional[str],
        typer.Option("--clients", "-c", help="Comma-separated client folder names to process."),
    ] = None,
    sync: Annotated[
        bool,
        typer.Option("--sync", help="Use sync API instead of batch (one at a time)."),
    ] = False,
    skip_conversion: Annotated[
        bool,
        typer.Option("--skip-conversion", help="Skip .doc → .docx conversion."),
    ] = False,
    spreadsheet_only: Annotated[
        bool,
        typer.Option("--spreadsheet-only", help="Only regenerate spreadsheet from existing extractions."),
    ] = False,
) -> None:
    """Phase 1: Parse documents, extract pricing via Claude API, generate spreadsheet."""
    from doc_pipeline.phases.extraction import run_extraction

    config = load_config()

    # Prerequisite checks
    if not config.inventory_path.exists():
        console.print("[red]Inventar nije pronađen. Pokrenite 'pipeline setup' prvo.[/red]")
        console.print("[red]No inventory found. Run 'pipeline setup' first.[/red]")
        raise typer.Exit(1)

    if not config.data_source_path.exists():
        console.print("[red]Radna kopija nije pronađena. Pokrenite 'pipeline setup' prvo.[/red]")
        console.print("[red]Working copy (data/source/) not found. Run 'pipeline setup' first.[/red]")
        raise typer.Exit(1)

    client_names = [c.strip() for c in clients.split(",")] if clients else None
    run_extraction(
        config,
        force=force,
        client_names=client_names,
        sync_mode=sync,
        skip_conversion=skip_conversion,
        spreadsheet_only=spreadsheet_only,
    )


@app.command()
def status() -> None:
    """Show current pipeline state."""
    config = load_config()
    state_path = config.project_root / "runs"

    if not state_path.exists():
        console.print("[yellow]No runs found. Run 'pipeline setup' first.[/yellow]")
        raise typer.Exit()

    # Find most recent run
    run_dirs = sorted(state_path.iterdir(), reverse=True)
    if not run_dirs:
        console.print("[yellow]No runs found.[/yellow]")
        raise typer.Exit()

    from doc_pipeline.state import RunState

    latest_dir = run_dirs[0]
    state_file = latest_dir / "state.json"
    if not state_file.exists():
        console.print(f"[yellow]No state.json in {latest_dir}[/yellow]")
        raise typer.Exit()

    state = RunState.load(state_file)
    console.print(f"\n[bold]Pipeline Status[/bold] (run: {state.run_id})")
    console.print(f"  Created: {state.created_at:%Y-%m-%d %H:%M}")

    for phase_name, phase in state.phases.items():
        status_color = {
            "completed": "green",
            "running": "yellow",
            "failed": "red",
            "pending": "dim",
        }.get(phase.status, "")

        console.print(f"  {phase_name}: [{status_color}]{phase.status}[/{status_color}]", end="")
        if phase.started_at:
            console.print(f" (started {phase.started_at:%H:%M})", end="")
        if phase.completed_at:
            console.print(f" → {phase.completed_at:%H:%M}", end="")
        if phase.error:
            console.print(f" [red]Error: {phase.error}[/red]", end="")
        console.print()

    # Show inventory summary if available
    inv_path = config.inventory_path
    if inv_path.exists():
        inv = Inventory.load(inv_path)
        console.print(f"\n  Inventory: {inv.total_clients} clients, "
                      f"{inv.clients_with_contracts} with contracts, "
                      f"{inv.clients_with_annexes} with annexes")


@app.command()
def inventory(
    format: Annotated[
        str,
        typer.Option("--format", help="Output format: table or json."),
    ] = "table",
    flagged_only: Annotated[
        bool,
        typer.Option("--flagged-only", help="Show only flagged clients."),
    ] = False,
    doc_type: Annotated[
        Optional[str],
        typer.Option("--type", help="Filter by document type (e.g., annex, maintenance_contract)."),
    ] = None,
) -> None:
    """Print file inventory summary."""
    config = load_config()
    inv_path = config.inventory_path

    if not inv_path.exists():
        console.print("[yellow]No inventory found. Run 'pipeline setup' first.[/yellow]")
        raise typer.Exit(1)

    inv = Inventory.load(inv_path)

    if format == "json":
        import json
        if flagged_only:
            data = [c.model_dump() for c in inv.flagged_clients]
        else:
            data = [c.model_dump() for c in inv.clients]
        console.print_json(json.dumps(data, default=str, ensure_ascii=False))
        return

    # Table format
    console.print()
    print_inventory_summary(inv)
    console.print()

    if flagged_only:
        print_flagged_clients(inv)
    else:
        clients = inv.clients
        if doc_type:
            clients = [
                c for c in clients
                if any(f.doc_type.value == doc_type for f in c.selected_files)
            ]
        print_client_table(clients, title=f"All Clients ({len(clients)})")


@app.command()
def generate(
    start_number: Annotated[
        Optional[int],
        typer.Option(
            "--start-number", "-n",
            help="Starting sequence number for annex numbering (e.g., 30 → U-26-30).",
        ),
    ] = None,
    clients: Annotated[
        Optional[str],
        typer.Option("--clients", "-c", help="Comma-separated client folder names to process."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show preview only, don't write files."),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Overwrite existing output files."),
    ] = False,
) -> None:
    """Phase 3: Generate annex documents from approved spreadsheet rows."""
    from doc_pipeline.phases.generation import run_generation

    config = load_config()

    # Prompt for start number if not provided
    if start_number is None:
        start_number_str = console.input(
            "[bold]Enter starting annex sequence number "
            "(e.g., 30 for U-26-30): [/bold]"
        )
        try:
            start_number = int(start_number_str.strip())
        except ValueError:
            console.print("[red]Invalid number.[/red]")
            raise typer.Exit(1)

    client_names = [c.strip() for c in clients.split(",")] if clients else None
    run_generation(
        config,
        start_number=start_number,
        client_names=client_names,
        dry_run=dry_run,
        force=force,
    )


@app.command(name="validate-template")
def validate_template() -> None:
    """Check template has all required Jinja2 placeholders."""
    from doc_pipeline.phases.generation import validate_template as _validate

    config = load_config()
    console.print(f"[bold]Validating template:[/bold] {config.template_path}")

    is_valid, issues = _validate(config.template_path)

    for issue in issues:
        if issue.startswith("Missing"):
            console.print(f"  [red]{issue}[/red]")
        else:
            console.print(f"  [dim]{issue}[/dim]")

    if is_valid:
        console.print("[bold green]Template is valid.[/bold green]")
    else:
        console.print("[bold red]Template validation failed.[/bold red]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
