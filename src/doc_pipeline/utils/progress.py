"""Rich progress bars and summary table formatting."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from doc_pipeline.models import ClientEntry, Inventory

console = Console()


def print_inventory_summary(inventory: Inventory) -> None:
    """Print a Rich summary table of the inventory."""
    table = Table(title="Pipeline Inventory Summary", show_lines=True)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Total clients", str(inventory.total_clients))
    table.add_row("With maintenance contracts", str(inventory.clients_with_contracts))
    table.add_row("With annexes", str(inventory.clients_with_annexes))
    table.add_row("Flagged", str(len(inventory.flagged_clients)))

    # Count by status
    from doc_pipeline.models import ClientStatus

    for status in ClientStatus:
        count = sum(1 for c in inventory.clients if c.status == status)
        if count > 0:
            table.add_row(f"  Status: {status.value}", str(count))

    console.print(table)


def print_client_table(
    clients: list[ClientEntry],
    *,
    title: str = "Clients",
    show_files: bool = False,
) -> None:
    """Print a table of clients."""
    table = Table(title=title, show_lines=True)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Client", style="bold")
    table.add_column("Status")
    table.add_column("Files", justify="right")
    table.add_column("Selected", justify="right")
    table.add_column("Contract")
    table.add_column("Annexes", justify="right")
    table.add_column("Flags")

    for i, client in enumerate(clients, 1):
        chain = client.document_chain
        main = chain.main_contract.split("/")[-1] if chain and chain.main_contract else "—"
        n_annex = len(chain.annexes) if chain else 0
        flags = ", ".join(client.flags) if client.flags else "—"

        status_style = {
            "ok": "green",
            "empty": "red",
            "no_contract": "yellow",
            "terminated": "red",
            "flagged": "yellow",
        }.get(client.status.value, "")

        table.add_row(
            str(i),
            client.client_name,
            f"[{status_style}]{client.status.value}[/{status_style}]",
            str(len(client.files)),
            str(len(client.selected_files)),
            main if len(main) <= 40 else main[:37] + "...",
            str(n_annex),
            flags if len(flags) <= 50 else flags[:47] + "...",
        )

    console.print(table)


def print_flagged_clients(inventory: Inventory) -> None:
    """Print only flagged clients with their flags."""
    flagged = inventory.flagged_clients
    if not flagged:
        console.print("[green]No flagged clients.[/green]")
        return
    print_client_table(flagged, title=f"Flagged Clients ({len(flagged)})")
