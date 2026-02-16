"""Phase 0: Setup — validate source, copy files, scan, classify, dedup, build inventory."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from doc_pipeline.config import PipelineConfig
from doc_pipeline.models import Inventory
from doc_pipeline.state import load_or_create_state
from doc_pipeline.utils.fileops import copy_source_tree, discover_clients
from doc_pipeline.utils.progress import (
    print_flagged_clients,
    print_inventory_summary,
)

console = Console()


def run_setup(
    config: PipelineConfig,
    source: Path | None = None,
    *,
    force: bool = False,
    scan_only: bool = False,
    dry_run: bool = False,
) -> Inventory:
    """Run Phase 0: setup the local working copy and build inventory.

    Args:
        config: Pipeline configuration.
        source: Override source path (default: from config).
        force: Overwrite existing working copy.
        scan_only: Skip copy, just scan existing data/source/.
        dry_run: Show what would happen without making changes.
    """
    source_path = source or config.source_path
    dest_path = config.data_source_path

    # ── Validate source ───────────────────────────────────────────────────
    console.print(f"\n[bold]Phase 0: Setup[/bold]")
    console.print(f"  Source: {source_path}")
    console.print(f"  Working copy: {dest_path}")

    if not source_path.exists():
        console.print(f"[red]Error: Source path does not exist: {source_path}[/red]")
        raise SystemExit(1)

    if not source_path.is_dir():
        console.print(f"[red]Error: Source path is not a directory: {source_path}[/red]")
        raise SystemExit(1)

    # ── Load/create run state ─────────────────────────────────────────────
    state, state_path = load_or_create_state(config.project_root)
    state.mark_started("setup")

    try:
        # ── Step 1: Copy source tree ──────────────────────────────────────
        if scan_only:
            if not dest_path.exists():
                console.print(
                    "[red]Error: --scan-only specified but working copy "
                    f"does not exist: {dest_path}[/red]"
                )
                raise SystemExit(1)
            console.print("  [dim]Skipping copy (--scan-only)[/dim]")
        elif dry_run:
            console.print("  [dim]Would copy source tree (--dry-run)[/dim]")
        else:
            if dest_path.exists() and not force:
                console.print(
                    f"\n[yellow]Working copy already exists at: {dest_path}[/yellow]"
                )
                console.print(
                    "  Use [bold]--force[/bold] to overwrite, or "
                    "[bold]--scan-only[/bold] to re-scan without copying."
                )
                raise SystemExit(1)

            console.print("\n  Copying source tree...")
            copied, skipped = copy_source_tree(source_path, dest_path, force=force)
            console.print(f"  [green]Copied {copied} files[/green] (skipped {skipped})")

        if dry_run:
            console.print("  [dim]Would scan and classify files (--dry-run)[/dim]")
            state.mark_completed("setup")
            state.save(state_path)
            raise SystemExit(0)

        # ── Step 2: Scan, classify, dedup, chain ──────────────────────────
        console.print("\n  Scanning and classifying files...")
        clients = discover_clients(dest_path)

        # ── Step 3: Build and save inventory ──────────────────────────────
        inventory = Inventory(
            source_path=str(source_path),
            working_path=str(dest_path),
            clients=clients,
        )

        inventory_path = config.inventory_path
        inventory.save(inventory_path)
        console.print(f"\n  [green]Inventory saved to: {inventory_path}[/green]")

        # ── Step 4: Print summary ─────────────────────────────────────────
        console.print()
        print_inventory_summary(inventory)
        console.print()
        print_flagged_clients(inventory)

        state.mark_completed("setup")
        state.save(state_path)

        return inventory

    except SystemExit:
        raise
    except Exception as e:
        state.mark_failed("setup", str(e))
        state.save(state_path)
        console.print(f"\n[red]Phase 0 failed: {e}[/red]")
        raise
