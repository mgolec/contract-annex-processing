"""Rich progress bars, summary table formatting, and progress tracking utilities."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from doc_pipeline.models import ClientEntry, Inventory

console = Console()


class ProgressTracker:
    """Simple progress tracker for pipeline operations.

    Usable by both GUI and CLI code to track the state of a multi-step
    operation without coupling to any particular rendering backend.
    """

    def __init__(self, total: int, description: str = "Processing") -> None:
        self.total = total
        self.current = 0
        self.description = description

    def advance(self, amount: int = 1) -> None:
        self.current = min(self.current + amount, self.total)

    def reset(self) -> None:
        self.current = 0

    @property
    def percentage(self) -> float:
        return (self.current / self.total * 100) if self.total > 0 else 0

    @property
    def is_complete(self) -> bool:
        return self.current >= self.total

    def __str__(self) -> str:
        return f"{self.description}: {self.current}/{self.total} ({self.percentage:.0f}%)"

    def __repr__(self) -> str:
        return (
            f"ProgressTracker(total={self.total}, current={self.current}, "
            f"description={self.description!r})"
        )


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


def _truncate(text: str, width: int) -> str:
    """Truncate *text* to *width* characters, appending '...' if needed."""
    if len(text) <= width:
        return text
    return text[: max(width - 3, 0)] + "..."


def print_client_table(
    clients: list[ClientEntry],
    *,
    title: str = "Clients",
    show_files: bool = False,
) -> None:
    """Print a table of clients."""
    # Dynamically size the three variable-width columns based on terminal width.
    # Fixed columns (#, Status, Files, Selected, Annexes) take ~30 chars combined.
    term_width = shutil.get_terminal_size().columns
    variable_space = term_width - 30
    name_width = max(20, variable_space // 3)
    main_width = max(20, variable_space // 3)
    flags_width = max(20, variable_space // 3)

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
            _truncate(client.client_name, name_width),
            f"[{status_style}]{client.status.value}[/{status_style}]",
            str(len(client.files)),
            str(len(client.selected_files)),
            _truncate(main, main_width),
            str(n_annex),
            _truncate(flags, flags_width),
        )

    console.print(table)


def print_flagged_clients(inventory: Inventory) -> None:
    """Print only flagged clients with their flags."""
    flagged = inventory.flagged_clients
    if not flagged:
        console.print("[green]No flagged clients.[/green]")
        return
    print_client_table(flagged, title=f"Flagged Clients ({len(flagged)})")
