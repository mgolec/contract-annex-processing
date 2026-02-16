"""Phase 1: Document parsing, Claude API extraction, and spreadsheet generation."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

from doc_pipeline.config import PipelineConfig
from doc_pipeline.models import (
    ClientEntry,
    ClientExtraction,
    ClientStatus,
    ConfidenceLevel,
    Currency,
    ExtractionResult,
    Inventory,
    PricingItem,
)
from doc_pipeline.state import load_or_create_state
from doc_pipeline.utils.croatian import nfc, parse_hr_number
from doc_pipeline.utils.parsers import (
    convert_doc_to_docx,
    extract_docx_text,
    extract_pdf_text,
    find_libreoffice,
)

console = Console()


# ── Extraction prompt ────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a document analysis assistant specialized in Croatian legal contracts.
You extract structured pricing data from IT service maintenance contracts and their annexes.

These contracts follow a highly standardized structure:
- They are between Procudo d.o.o. (IT service provider) and a client company.
- Pricing is typically in a table called "Prilog 2" or "Prilog 1" with these columns:
  Poz. | Opis usluge | Oznaka | Mjera | Kol. | Jed. Cijena
- Common line items: monthly maintenance (paušal), L1 hourly rate, L2/L3 hourly rate, on-site visit.
- Annexes modify pricing from the parent contract and reference it.

Key instructions:
- Look for [TABLE] markers in the text — they contain structured table data.
- The pricing table usually has headers containing "Poz" and "cijena" (price).
- Keep prices in original Croatian format: dot = thousands separator, comma = decimal (e.g., "1.200,00").
- Detect the currency from the table header: "(EUR)" means EUR, "(kn)" or "(HRK)" means HRK.
- Contracts from before 2023 may use HRK (Croatian Kuna). From 2023 onward, EUR.
- Extract ALL pricing rows, not just the monthly fee.
- Set confidence to "high" if you find a clear pricing table, "medium" if pricing is in prose, "low" if uncertain.
- If this is an annex, set document_type to "annex" and try to identify the parent contract number.
- The document_date should be the signing/effective date found in the document header.
"""

USER_PROMPT_TEMPLATE = """\
Extract the structured pricing data from the following Croatian contract document.
The document belongs to client folder: "{folder_name}".

DOCUMENT TEXT:
---
{document_text}
---

Call the extract_contract_data tool with the extracted information.
"""


# ── Tool schema for Claude API ───────────────────────────────────────────────

def _build_tool_schema() -> dict:
    """Build the Claude API tool definition from ExtractionResult schema."""
    return {
        "name": "extract_contract_data",
        "description": "Extract structured pricing data from a Croatian IT service contract or annex.",
        "input_schema": {
            "type": "object",
            "properties": {
                "client_name": {
                    "type": "string",
                    "description": "Client company name as stated in the contract.",
                },
                "client_oib": {
                    "type": "string",
                    "description": "Client OIB (11-digit tax ID). Empty string if not found.",
                },
                "document_type": {
                    "type": "string",
                    "enum": ["contract", "annex"],
                    "description": "Whether this is a main contract or an annex/amendment.",
                },
                "contract_number": {
                    "type": "string",
                    "description": "Contract/annex reference number (e.g., 'U-25-09'). Empty if not found.",
                },
                "parent_contract_number": {
                    "type": "string",
                    "description": "For annexes: the parent contract number this annex modifies. Empty if not applicable.",
                },
                "document_date": {
                    "type": "string",
                    "description": "Signing or effective date as written in the document (e.g., '01.03.2025'). Empty if not found.",
                },
                "pricing_items": {
                    "type": "array",
                    "description": "All pricing line items found in the document.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "position": {
                                "type": "string",
                                "description": "Row number/position (e.g., '1.', '2.').",
                            },
                            "service_name": {
                                "type": "string",
                                "description": "Service description in Croatian.",
                            },
                            "designation": {
                                "type": "string",
                                "description": "Service designation/type (e.g., 'paušal').",
                            },
                            "unit": {
                                "type": "string",
                                "description": "Unit of measurement (e.g., 'sat', 'kom', 'mjesečno').",
                            },
                            "quantity": {
                                "type": "string",
                                "description": "Quantity (e.g., '1').",
                            },
                            "price_raw": {
                                "type": "string",
                                "description": "Price as written in Croatian format (e.g., '1.200,00'). Keep original formatting.",
                            },
                            "source_section": {
                                "type": "string",
                                "description": "Where this item was found (e.g., 'Prilog 2', 'TABLE 0', 'Članak 5').",
                            },
                        },
                        "required": ["service_name", "price_raw"],
                    },
                },
                "currency": {
                    "type": "string",
                    "enum": ["EUR", "HRK"],
                    "description": "Currency of the prices. EUR for post-2023, HRK for pre-2023.",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "Extraction confidence: high=clear table, medium=prose pricing, low=uncertain.",
                },
                "notes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Any observations, warnings, or ambiguities found during extraction.",
                },
            },
            "required": [
                "client_name",
                "document_type",
                "pricing_items",
                "currency",
                "confidence",
            ],
        },
    }


# ── Parse Claude response into model ────────────────────────────────────────


def _parse_extraction_response(tool_input: dict) -> ExtractionResult:
    """Parse the tool_use input dict from Claude into an ExtractionResult model."""
    # Parse pricing items and compute price_value from price_raw
    items = []
    for raw_item in tool_input.get("pricing_items", []):
        price_val = parse_hr_number(raw_item.get("price_raw", ""))
        item = PricingItem(
            position=raw_item.get("position", ""),
            service_name=raw_item.get("service_name", ""),
            designation=raw_item.get("designation", ""),
            unit=raw_item.get("unit", ""),
            quantity=raw_item.get("quantity", ""),
            price_raw=raw_item.get("price_raw", ""),
            price_value=price_val,
            currency=Currency(tool_input.get("currency", "EUR")),
            source_section=raw_item.get("source_section", ""),
        )
        items.append(item)

    return ExtractionResult(
        client_name=tool_input.get("client_name", ""),
        client_oib=tool_input.get("client_oib", ""),
        document_type=tool_input.get("document_type", ""),
        contract_number=tool_input.get("contract_number", ""),
        parent_contract_number=tool_input.get("parent_contract_number", ""),
        document_date=tool_input.get("document_date", ""),
        pricing_items=items,
        currency=Currency(tool_input.get("currency", "EUR")),
        confidence=ConfidenceLevel(tool_input.get("confidence", "medium")),
        notes=tool_input.get("notes", []),
    )


# ── Sync extraction (one client at a time) ──────────────────────────────────


def _extract_sync(
    client,
    model: str,
    folder_name: str,
    document_text: str,
) -> ExtractionResult:
    """Call Claude API synchronously for a single document."""
    tool = _build_tool_schema()

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        tools=[tool],
        tool_choice={"type": "tool", "name": "extract_contract_data"},
        messages=[
            {
                "role": "user",
                "content": USER_PROMPT_TEMPLATE.format(
                    folder_name=folder_name,
                    document_text=document_text[:100_000],  # Safety limit
                ),
            }
        ],
    )

    # Find the tool_use content block
    for block in response.content:
        if block.type == "tool_use" and block.name == "extract_contract_data":
            result = _parse_extraction_response(block.input)
            result.raw_text_length = len(document_text)
            return result

    raise ValueError("No tool_use block found in Claude response")


# ── Batch extraction ─────────────────────────────────────────────────────────


def _extract_batch(
    client,
    model: str,
    requests: list[tuple[str, str, str]],  # (custom_id, folder_name, document_text)
) -> dict[str, ExtractionResult | str]:
    """Submit a batch of extraction requests and poll until complete.

    Args:
        client: Anthropic API client.
        model: Model ID.
        requests: List of (custom_id, folder_name, document_text) tuples.

    Returns:
        Dict mapping custom_id → ExtractionResult (success) or error string.
    """
    tool = _build_tool_schema()

    # Build batch requests
    batch_requests = []
    for custom_id, folder_name, document_text in requests:
        batch_requests.append({
            "custom_id": custom_id,
            "params": {
                "model": model,
                "max_tokens": 4096,
                "system": SYSTEM_PROMPT,
                "tools": [tool],
                "tool_choice": {"type": "tool", "name": "extract_contract_data"},
                "messages": [
                    {
                        "role": "user",
                        "content": USER_PROMPT_TEMPLATE.format(
                            folder_name=folder_name,
                            document_text=document_text[:100_000],
                        ),
                    }
                ],
            },
        })

    # Submit batch
    console.print(f"\n  Submitting batch of {len(batch_requests)} requests...")
    batch = client.messages.batches.create(requests=batch_requests)
    batch_id = batch.id
    console.print(f"  Batch ID: {batch_id}")

    # Poll until complete (max 30 minutes)
    max_wait = 30 * 60
    poll_interval = 30
    elapsed = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        transient=True,
    ) as progress:
        task = progress.add_task("Waiting for batch completion...", total=None)

        while elapsed < max_wait:
            batch = client.messages.batches.retrieve(batch_id)
            counts = batch.request_counts

            progress.update(
                task,
                description=(
                    f"Batch: {counts.succeeded} succeeded, "
                    f"{counts.errored} errored, "
                    f"{counts.processing} processing"
                ),
            )

            if batch.processing_status == "ended":
                break

            time.sleep(poll_interval)
            elapsed += poll_interval

    if batch.processing_status != "ended":
        raise TimeoutError(f"Batch {batch_id} did not complete within {max_wait}s")

    # Retrieve results
    results: dict[str, ExtractionResult | str] = {}

    for result in client.messages.batches.results(batch_id):
        custom_id = result.custom_id

        if result.result.type == "succeeded":
            message = result.result.message
            extracted = False
            for block in message.content:
                if block.type == "tool_use" and block.name == "extract_contract_data":
                    try:
                        extraction = _parse_extraction_response(block.input)
                        results[custom_id] = extraction
                        extracted = True
                    except Exception as e:
                        results[custom_id] = f"Parse error: {e}"
                    break
            if not extracted and custom_id not in results:
                results[custom_id] = "No tool_use block in response"
        else:
            error_type = result.result.type
            results[custom_id] = f"Batch error: {error_type}"

    return results


# ── Text extraction for a client ─────────────────────────────────────────────


def _get_document_text(
    client_entry: ClientEntry,
    config: PipelineConfig,
    *,
    skip_conversion: bool = False,
) -> tuple[str, str, str, bool]:
    """Extract text from a client's latest valid document.

    Returns:
        (text, source_file, source_extension, was_converted)
    """
    chain = client_entry.document_chain
    if not chain or not chain.latest_valid_document:
        raise ValueError(f"No latest valid document for {client_entry.folder_name}")

    rel_path = chain.latest_valid_document
    source_file = config.data_source_path / rel_path
    ext = source_file.suffix.lower()
    was_converted = False

    if ext == ".docx":
        text = extract_docx_text(source_file)
    elif ext == ".doc":
        if skip_conversion:
            raise ValueError(f"Skipping .doc conversion for {source_file.name}")
        # Convert .doc → .docx first
        # Put converted file in data/converted/{folder_name}/
        out_dir = config.converted_path / client_entry.folder_name
        converted = convert_doc_to_docx(source_file, out_dir)
        if converted is None:
            raise ValueError(
                f"LibreOffice conversion failed for {source_file.name}. "
                "Is LibreOffice installed?"
            )
        text = extract_docx_text(converted)
        was_converted = True
    elif ext == ".pdf":
        text = extract_pdf_text(source_file)
    else:
        raise ValueError(f"Unsupported extension: {ext}")

    return text, rel_path, ext, was_converted


# ── Main orchestrator ────────────────────────────────────────────────────────


def run_extraction(
    config: PipelineConfig,
    *,
    force: bool = False,
    client_names: list[str] | None = None,
    sync_mode: bool = False,
    skip_conversion: bool = False,
    spreadsheet_only: bool = False,
) -> list[ClientExtraction]:
    """Run Phase 1: extract pricing data from contracts and generate spreadsheet.

    Args:
        config: Pipeline configuration.
        force: Re-extract even if JSON already exists.
        client_names: Process only these clients (by folder name).
        sync_mode: Use sync API instead of batch.
        skip_conversion: Skip .doc → .docx conversion.
        spreadsheet_only: Only regenerate spreadsheet from existing extractions.
    """
    import anthropic

    console.print("\n[bold]Phase 1: Extraction[/bold]")

    # Load inventory
    inv_path = config.inventory_path
    if not inv_path.exists():
        console.print("[red]Error: No inventory found. Run 'pipeline setup' first.[/red]")
        raise SystemExit(1)

    inventory = Inventory.load(inv_path)

    # Load/create run state
    state, state_path = load_or_create_state(config.project_root)
    state.mark_started("extraction")

    try:
        # Filter to extractable clients
        extractable_statuses = {ClientStatus.OK, ClientStatus.FLAGGED}
        clients = [
            c for c in inventory.clients
            if c.status in extractable_statuses
            and c.document_chain
            and c.document_chain.latest_valid_document
        ]

        # Filter by client names if specified
        if client_names:
            name_set = {n.strip() for n in client_names}
            clients = [c for c in clients if c.folder_name in name_set]
            not_found = name_set - {c.folder_name for c in clients}
            if not_found:
                console.print(f"  [yellow]Clients not found: {', '.join(sorted(not_found))}[/yellow]")

        console.print(f"  Extractable clients: {len(clients)}")

        # Ensure output directories exist
        config.extractions_path.mkdir(parents=True, exist_ok=True)
        config.output_path.mkdir(parents=True, exist_ok=True)

        # If spreadsheet_only, just load existing extractions and generate
        if spreadsheet_only:
            extractions = _load_existing_extractions(config, clients)
            console.print(f"  Loaded {len(extractions)} existing extractions")
            _generate_spreadsheet_wrapper(extractions, inventory, config)
            state.mark_completed("extraction")
            state.save(state_path)
            return extractions

        # Check for API key
        if not config.anthropic_api_key:
            console.print("[red]Error: ANTHROPIC_API_KEY not set. Check .env file.[/red]")
            raise SystemExit(1)

        api_client = anthropic.Anthropic(api_key=config.anthropic_api_key)

        # Check LibreOffice availability for .doc files
        doc_clients = [
            c for c in clients
            if c.document_chain
            and c.document_chain.latest_valid_document
            and Path(c.document_chain.latest_valid_document).suffix.lower() == ".doc"
        ]
        if doc_clients and not skip_conversion:
            lo = find_libreoffice()
            if not lo:
                console.print(
                    f"  [yellow]Warning: LibreOffice not found. "
                    f"Skipping {len(doc_clients)} .doc clients.[/yellow]"
                )
                clients = [c for c in clients if c not in doc_clients]
            else:
                console.print(f"  LibreOffice found: {lo}")
                console.print(f"  .doc clients to convert: {len(doc_clients)}")

        # ── Step 1: Extract text from all documents ──────────────────────
        console.print("\n  Extracting text from documents...")
        texts: dict[str, tuple[str, str, str, bool]] = {}  # folder → (text, file, ext, converted)
        skipped_existing = 0

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            transient=True,
        ) as progress:
            task = progress.add_task("Extracting text...", total=len(clients))

            for client_entry in clients:
                progress.update(task, advance=1, description=f"Text: {client_entry.folder_name}")
                folder = client_entry.folder_name

                # Skip if already extracted (unless --force)
                json_path = config.extractions_path / f"{folder}.json"
                if json_path.exists() and not force:
                    skipped_existing += 1
                    continue

                try:
                    text, source_file, ext, was_conv = _get_document_text(
                        client_entry, config, skip_conversion=skip_conversion,
                    )
                    texts[folder] = (text, source_file, ext, was_conv)
                except Exception as e:
                    # Save error immediately
                    ce = ClientExtraction(
                        folder_name=folder,
                        source_file=client_entry.document_chain.latest_valid_document or "",
                        source_extension=Path(
                            client_entry.document_chain.latest_valid_document or ""
                        ).suffix.lower(),
                        extracted_at=datetime.now(),
                        error=str(e),
                    )
                    ce.save(json_path)
                    console.print(f"    [red]Error ({folder}): {e}[/red]")

        if skipped_existing:
            console.print(f"  [dim]Skipped {skipped_existing} already extracted (use --force to re-extract)[/dim]")

        if not texts:
            console.print("  [dim]No new documents to extract.[/dim]")
            extractions = _load_existing_extractions(config, clients)
            _generate_spreadsheet_wrapper(extractions, inventory, config)
            state.mark_completed("extraction")
            state.save(state_path)
            return extractions

        console.print(f"  Documents to send to Claude API: {len(texts)}")

        # ── Step 2: Call Claude API ──────────────────────────────────────
        extractions: list[ClientExtraction] = []

        if sync_mode:
            extractions = _run_sync_extraction(
                api_client, config, texts,
            )
        else:
            extractions = _run_batch_extraction(
                api_client, config, texts,
            )

        # Load previously extracted clients too
        all_extractions = _load_existing_extractions(config, clients)

        # ── Step 3: Generate spreadsheet ─────────────────────────────────
        _generate_spreadsheet_wrapper(all_extractions, inventory, config)

        # ── Summary ──────────────────────────────────────────────────────
        _print_summary(all_extractions)

        state.mark_completed("extraction")
        state.save(state_path)
        return all_extractions

    except SystemExit:
        raise
    except Exception as e:
        state.mark_failed("extraction", str(e))
        state.save(state_path)
        console.print(f"\n[red]Phase 1 failed: {e}[/red]")
        raise


# ── Sync mode runner ─────────────────────────────────────────────────────────


def _run_sync_extraction(
    api_client,
    config: PipelineConfig,
    texts: dict[str, tuple[str, str, str, bool]],
) -> list[ClientExtraction]:
    """Run extraction in sync mode (one API call per client)."""
    extractions: list[ClientExtraction] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
    ) as progress:
        task = progress.add_task("Claude API extraction...", total=len(texts))

        for folder, (text, source_file, ext, was_conv) in texts.items():
            progress.update(task, advance=1, description=f"API: {folder}")

            ce = ClientExtraction(
                folder_name=folder,
                source_file=source_file,
                source_extension=ext,
                was_converted=was_conv,
                extracted_at=datetime.now(),
            )

            try:
                result = _extract_sync(
                    api_client, config.extraction.model, folder, text,
                )
                result.raw_text_length = len(text)
                ce.extraction = result
            except Exception as e:
                ce.error = str(e)
                console.print(f"    [red]Error ({folder}): {e}[/red]")

            # Save per-client JSON
            json_path = config.extractions_path / f"{folder}.json"
            ce.save(json_path)
            extractions.append(ce)

    return extractions


# ── Batch mode runner ────────────────────────────────────────────────────────


def _sanitize_custom_id(folder_name: str) -> str:
    """Sanitize folder name into a valid batch custom_id (alphanumeric, _, -)."""
    import re
    # Replace non-alphanumeric chars (except _ and -) with _
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", folder_name)
    # Truncate to 64 chars
    return sanitized[:64]


def _run_batch_extraction(
    api_client,
    config: PipelineConfig,
    texts: dict[str, tuple[str, str, str, bool]],
) -> list[ClientExtraction]:
    """Run extraction in batch mode (all at once, poll for results)."""
    # Build batch requests — sanitize custom_id and maintain mapping
    batch_requests: list[tuple[str, str, str]] = []
    meta: dict[str, tuple[str, str, bool]] = {}  # folder → (source_file, ext, was_converted)
    id_to_folder: dict[str, str] = {}  # sanitized_id → folder_name

    for folder, (text, source_file, ext, was_conv) in texts.items():
        custom_id = _sanitize_custom_id(folder)
        # Handle potential collisions by appending index
        if custom_id in id_to_folder:
            custom_id = f"{custom_id}_{hash(folder) % 10000}"
        batch_requests.append((custom_id, folder, text))
        id_to_folder[custom_id] = folder
        meta[folder] = (source_file, ext, was_conv)

    # Submit and wait
    results = _extract_batch(api_client, config.extraction.model, batch_requests)

    # Map results back to folder names
    folder_results: dict[str, ExtractionResult | str] = {}
    for cid, result in results.items():
        folder = id_to_folder.get(cid, cid)
        folder_results[folder] = result

    # Process results
    extractions: list[ClientExtraction] = []
    succeeded = 0
    failed = 0

    for folder in texts:
        source_file, ext, was_conv = meta[folder]
        text = texts[folder][0]

        ce = ClientExtraction(
            folder_name=folder,
            source_file=source_file,
            source_extension=ext,
            was_converted=was_conv,
            extracted_at=datetime.now(),
        )

        result = folder_results.get(folder)
        if isinstance(result, ExtractionResult):
            result.raw_text_length = len(text)
            ce.extraction = result
            succeeded += 1
        elif isinstance(result, str):
            ce.error = result
            failed += 1
        else:
            ce.error = "No result returned from batch"
            failed += 1

        # Save per-client JSON
        json_path = config.extractions_path / f"{folder}.json"
        ce.save(json_path)
        extractions.append(ce)

    console.print(f"\n  Batch results: {succeeded} succeeded, {failed} failed")
    return extractions


# ── Load existing extractions ────────────────────────────────────────────────


def _load_existing_extractions(
    config: PipelineConfig,
    clients: list[ClientEntry],
) -> list[ClientExtraction]:
    """Load all existing extraction JSONs for the given clients."""
    extractions: list[ClientExtraction] = []

    for client_entry in clients:
        json_path = config.extractions_path / f"{client_entry.folder_name}.json"
        if json_path.exists():
            try:
                ce = ClientExtraction.load(json_path)
                extractions.append(ce)
            except Exception:
                pass

    return extractions


# ── Spreadsheet wrapper ──────────────────────────────────────────────────────


def _generate_spreadsheet_wrapper(
    extractions: list[ClientExtraction],
    inventory: Inventory,
    config: PipelineConfig,
) -> None:
    """Generate the control spreadsheet."""
    from doc_pipeline.phases.spreadsheet import generate_spreadsheet

    if not extractions:
        console.print("  [yellow]No extractions to write to spreadsheet.[/yellow]")
        return

    path = generate_spreadsheet(extractions, inventory, config)
    console.print(f"\n  [green]Spreadsheet saved to: {path}[/green]")


# ── Summary printing ─────────────────────────────────────────────────────────


def _print_summary(extractions: list[ClientExtraction]) -> None:
    """Print a summary of extraction results."""
    from rich.table import Table

    total = len(extractions)
    success = sum(1 for e in extractions if e.extraction and not e.error)
    errors = sum(1 for e in extractions if e.error)
    high = sum(
        1 for e in extractions
        if e.extraction and e.extraction.confidence == ConfidenceLevel.HIGH
    )
    medium = sum(
        1 for e in extractions
        if e.extraction and e.extraction.confidence == ConfidenceLevel.MEDIUM
    )
    low = sum(
        1 for e in extractions
        if e.extraction and e.extraction.confidence == ConfidenceLevel.LOW
    )
    hrk_count = sum(
        1 for e in extractions
        if e.extraction and e.extraction.currency == Currency.HRK
    )

    table = Table(title="Extraction Summary", show_lines=True)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Total clients", str(total))
    table.add_row("Successful", f"[green]{success}[/green]")
    table.add_row("Errors", f"[red]{errors}[/red]" if errors else "0")
    table.add_row("Confidence: high", str(high))
    table.add_row("Confidence: medium", str(medium))
    table.add_row("Confidence: low", str(low))
    if hrk_count:
        table.add_row("HRK contracts", f"[yellow]{hrk_count}[/yellow]")

    console.print()
    console.print(table)

    # Show errors if any
    if errors:
        console.print("\n[bold red]Errors:[/bold red]")
        for e in extractions:
            if e.error:
                console.print(f"  {e.folder_name}: {e.error}")
