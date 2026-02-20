# Architecture Overview

End-to-end system for automating annual price adjustments across ~90 Croatian-language client contracts. The pipeline reads existing contracts, extracts pricing with AI, produces a human-review spreadsheet, and generates new legal annex documents.

---

## System Diagram

```
                         ┌─────────────────────────────┐
                         │    User Interface Layer      │
                         │                              │
                         │  ┌─────────┐  ┌───────────┐ │
                         │  │ GUI     │  │ CLI       │ │
                         │  │ tkinter │  │ typer+Rich│ │
                         │  │ wizard  │  │ commands  │ │
                         │  └────┬────┘  └─────┬─────┘ │
                         └───────┼─────────────┼───────┘
                                 │             │
                                 ▼             ▼
                         ┌─────────────────────────────┐
                         │      Configuration Layer     │
                         │                              │
                         │  pipeline.toml  +  .env      │
                         │         │                    │
                         │    PipelineConfig (Pydantic) │
                         └────────────┬────────────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                  ▼
           ┌──────────────┐ ┌────────────────┐ ┌──────────────┐
           │   Phase 0    │ │    Phase 1     │ │   Phase 3    │
           │    Setup     │ │  Extraction    │ │  Generation  │
           │              │ │                │ │              │
           │ • Copy files │ │ • Parse docs   │ │ • Read back  │
           │ • Classify   │ │ • Claude API   │ │   spreadsheet│
           │ • Inventory  │ │ • Spreadsheet  │ │ • Render     │
           │              │ │                │ │   templates  │
           └──────┬───────┘ └───────┬────────┘ └──────┬───────┘
                  │                 │                  │
                  ▼                 ▼                  ▼
           ┌─────────────────────────────────────────────────┐
           │              Utility Layer                       │
           │                                                  │
           │  fileops.py   parsers.py   croatian.py           │
           │  (discover,   (docx/doc/   (NFC, dates,          │
           │   classify,    pdf text     numbers,              │
           │   dedup)       extraction)  formatting)           │
           └─────────────────────────────────────────────────┘
                  │                 │                  │
                  ▼                 ▼                  ▼
           ┌─────────────────────────────────────────────────┐
           │             External Services & I/O              │
           │                                                  │
           │  contracts/    LibreOffice     Claude API         │
           │  (source       (doc→docx       (structured        │
           │   .docx/.doc)   conversion)     extraction)       │
           │                                                  │
           │  data/         output/         templates/         │
           │  (inventory,   (spreadsheet,   (aneks_template    │
           │   extractions)  annexes/)       .docx)            │
           └─────────────────────────────────────────────────┘
```

---

## Data Flow — End to End

```
contracts/                    Source of truth (READ-ONLY)
    │
    │  Phase 0: Setup
    │  ─────────────
    ▼
data/source/                  Local copy of all client folders
    │
    │  discover_clients()     Scan, classify (regex), deduplicate (.docx > .doc > .pdf),
    │                         chain documents (main contract → annexes in order)
    ▼
data/inventory.json           Structured inventory: 82 clients, files, statuses, chains
    │
    │  Phase 1: Extraction
    │  ────────────────────
    │
    │  1. For each client with a maintenance contract:
    │     • Pick latest_valid_document from chain
    │     • Extract text: .docx → python-docx | .doc → LibreOffice → .docx | .pdf → pdfplumber
    │     • NFC normalize all Croatian text
    │
    │  2. Send to Claude API (Batch or Sync):
    │     • System prompt: structured extraction schema (English)
    │     • User content: full document text (Croatian, truncation warning >100K chars)
    │     • Response: JSON with client info, pricing table, confidence score
    │     • Retry: 3 attempts with exponential backoff on transient errors
    │     • Timeout: 120 seconds per request
    │
    ▼
data/extractions/*.json       One JSON per client: ExtractionResult with PricingItems
    │
    │  3. Generate spreadsheet from all extractions:
    │
    ▼
output/control_spreadsheet.xlsx
    │
    │  Sheet 1 "Pregled klijenata":  Client overview + Status column (editable)
    │  Sheet 2 "Cijene":             Pricing rows + "Nova cijena EUR" column (editable)
    │  Sheet 3 "Inventar":           File inventory (read-only reference)
    │
    │  Phase 2: Human Review (MANUAL)
    │  ─────────────────────────────
    │  User opens in Excel, marks approved clients as "Odobreno",
    │  enters new prices, saves and closes.
    │
    │  Phase 3: Generation
    │  ────────────────────
    │
    │  1. Read back spreadsheet:
    │     • Validate column headers before reading (prevents silent data corruption)
    │     • Sheet 1 → approved clients (status == "Odobreno")
    │     • Sheet 2 → new EUR prices per service per client (Decimal precision)
    │     • Fuzzy name-based matching (thefuzz, threshold 70) to pair prices with services
    │
    │  2. For each approved client:
    │     • Load extraction JSON
    │     • Parse source .docx for client director, address, hour fund
    │     • Build Jinja2 template context (30+ variables)
    │     • Convert HRK → EUR if needed (Decimal, rate from config)
    │     • Format all values Croatian-style (dates, numbers)
    │     • Auto-detect next annex number from existing output files
    │
    │  3. Render template via docxtpl:
    │
    ▼
output/annexes/{client}/Aneks_{U-26-NN}.docx    One annex per approved client
```

---

## Module Map

```
src/doc_pipeline/
├── __init__.py
├── config.py              PipelineConfig (Pydantic) — loads pipeline.toml + .env
│                          Validators: .env quote stripping, API key format,
│                          confidence threshold, date format. validate_for_extraction().
├── models.py              All data models: FileEntry, ClientEntry, Inventory,
│                          ExtractionResult, PricingItem, ClientExtraction, enums.
│                          Decimal price_value, NFC field validators, atomic save().
├── state.py               RunState — PhaseStatus enum (PENDING/RUNNING/COMPLETED/FAILED),
│                          atomic writes, crash recovery (check_stale_running),
│                          reset_phase(), reset_all().
├── cli.py                 Typer CLI — 7 commands (setup, extract, generate, status,
│                          inventory, validate-template, reset). --version, --verbose flags.
│                          Prerequisite checks, consistent exit codes.
├── gui.py                 Tkinter GUI — 5-step wizard. Thread-safe console, cancel button,
│                          step ordering, keyboard shortcuts, log search/export,
│                          client filter, extraction preview, API test button, tooltips.
├── utils/
│   ├── progress.py        Rich console, summary tables, ProgressTracker class,
│   │                      dynamic terminal-width column truncation.
│   ├── croatian.py        NFC normalization, hr_date(), hr_number(), month names,
│   │                      parse_hr_number() → Decimal.
│   ├── fileops.py         copy_source_tree() (atomic with rollback), discover_clients(),
│   │                      classify_file(), PipelineLock (fcntl.flock), disk space checks,
│   │                      symlink handling, DS_Store filtering, structured logging.
│   └── parsers.py         extract_docx_text(), convert_doc_to_docx() (temp LO profile
│                          with cleanup), extract_pdf_text(), nested table handling.
└── phases/
    ├── setup.py           Phase 0: run_setup() → Inventory
    ├── extraction.py      Phase 1: run_extraction() → list[ClientExtraction]
    │                      API retry with exponential backoff, 120s timeout,
    │                      batch error details, token truncation warning.
    ├── spreadsheet.py     Phase 1: generate_spreadsheet() — 3-sheet Excel with protection.
    │                      HRK rate from config, IFERROR formula wrappers.
    └── generation.py      Phase 3: run_generation() → list[Path]
                           Fuzzy name-based price matching (thefuzz), spreadsheet header
                           validation, Decimal throughout, auto annex number detection,
                           template rendering validation.
```

---

## Key Design Decisions

### 1. GUI and CLI share the same phase functions

Both `cli.py` and `gui.py` call the same `run_setup()`, `run_extraction()`, and `run_generation()` functions. The GUI adds:
- Background threading with `threading.Lock` on buffered console (thread-safe I/O)
- Rich console output capture (redirected to a text widget via `_BufferedConsole`)
- Auto-confirmation with `_SAFE_PROMPTS` whitelist (only auto-confirms known-safe prompts)
- Cancel button using `threading.Event` for cooperative cancellation
- Step ordering enforcement (`_STEP_DEPS` dict prevents running phases out of order)

### 2. Human-in-the-loop via Excel spreadsheet

Phase 2 is entirely manual. The spreadsheet is the handoff point:
- Pipeline writes it (openpyxl)
- User edits it in Excel (data validation, conditional formatting, sheet protection guide the user)
- Pipeline reads it back (openpyxl)

This was chosen over an in-app editor because:
- Users are comfortable with Excel
- Complex pricing review benefits from Excel's filtering/sorting
- No risk of data loss from app crashes during review

### 3. Claude API with structured extraction

Documents are sent as full text to Claude with a tool-use schema defining the exact output structure. This gives:
- Typed, validated responses via Pydantic
- Confidence scoring per extraction
- Batch API option (50% cost savings, ~30 min turnaround)

### 4. Template-based document generation

Annexes are generated from a real .docx template (copied from an actual client annex) with Jinja2 placeholders via `docxtpl`. This preserves:
- Exact formatting, fonts, styles from the original
- Dynamic table rows via `{%tr for %}` syntax
- Conditional sections (e.g. HRK conversion notice)

### 5. Immutable source, mutable working copy

`contracts/` is never modified. Phase 0 copies everything to `data/source/`. This means:
- Original files are always safe
- Pipeline can be re-run from scratch
- Working directory can be deleted and recreated

### 6. Croatian language handling

All text passes through NFC Unicode normalization (composed forms for č, ć, ž, š, đ). Dates use Croatian genitive month names. Numbers use Croatian formatting (dot = thousands, comma = decimal). Currency conversion uses `decimal.Decimal` for precision. NFC normalization is also enforced at the Pydantic model level via `@field_validator` on text fields (client_name, service_name, folder_name, etc.).

---

## File Format Pipeline

```
.docx ──────────────────────────────────► extract_docx_text()  ──► full text with [TABLE] markers
.doc  ──► LibreOffice (headless) ──► .docx ──► extract_docx_text()
.pdf  ──────────────────────────────────► extract_pdf_text()   ──► pdfplumber page text

Deduplication: if same document exists as .docx + .doc + .pdf, only .docx is used.
```

---

## Robustness & Safety

### Decimal currency pipeline

All price values use `decimal.Decimal` end-to-end to prevent floating-point rounding errors:
- `PricingItem.price_value: Decimal | None` (with `@field_validator` coercing float/str/int)
- `NewPrice.new_price_eur: Decimal` (spreadsheet read-back uses `Decimal(str(value))`)
- `parse_hr_number()` returns `Decimal | None`
- `hr_number()` accepts `float | Decimal`
- HRK→EUR conversion: `Decimal(str(config.currency.hrk_to_eur_rate))`

### API resilience

- **Retry with exponential backoff**: 3 retries with doubling delay for transient API errors (`APIConnectionError`, `RateLimitError`, `InternalServerError`)
- **120-second timeout** on `client.messages.create()`
- **Token truncation warning** when document text exceeds 100K characters
- **Batch error details**: Extracts specific error messages from failed batch results

### Atomic file writes

All critical data files use write-to-temp-then-rename to prevent corruption:
- `Inventory.save()` → writes to `.tmp`, then `os.replace()`
- `ClientExtraction.save()` → same pattern
- `RunState.save()` → same pattern
- `copy_source_tree()` → atomic copy with backup/rollback on failure

### Concurrent run protection

`PipelineLock` class in `fileops.py` uses `fcntl.flock()` to prevent multiple pipeline instances from running simultaneously. Acquired at phase entry, released on completion.

### Spreadsheet integrity

- **Header validation**: Validates column headers on Sheet 1 and Sheet 2 before reading data back, preventing silent data corruption from modified spreadsheets
- **Fuzzy name-based price matching**: Uses `thefuzz.fuzz.ratio()` (threshold 70) to match service names between extraction data and spreadsheet rows, instead of fragile positional index matching
- **IFERROR formula wrappers**: EUR conversion and % change formulas gracefully handle missing/invalid data
- **Python fallback for formula cells**: When `data_only=True` returns None (unopened in Excel), Python recalculates the value

### Template validation

- Checks rendered context for placeholder artifacts (`___`, `________`, `N/A`)
- Verifies required fields (korisnik_naziv, korisnik_oib, broj_ugovora) are populated
- Auto-detects next annex number by scanning output directory for existing annexes

### File operation safety

- **Disk space check** before bulk copy operations
- **Symlink handling**: `symlinks=True` during copy, skipped during scan
- **DS_Store / junk file filtering**: Case-insensitive `_ignore_junk()` filter
- **.env quote stripping**: Handles `API_KEY="sk-..."` format

---

## State Management

Each execution cycle is tracked in `runs/YYYY-MM/state.json` using the `PhaseStatus` enum (`PENDING`, `RUNNING`, `COMPLETED`, `FAILED`):

```json
{
  "run_id": "2026-02",
  "created_at": "2026-02-16T...",
  "phases": {
    "setup":      { "status": "completed", "started_at": "...", "completed_at": "..." },
    "extraction": { "status": "completed", "started_at": "...", "completed_at": "..." },
    "generation": { "status": "running",   "started_at": "..." }
  }
}
```

State writes are atomic (temp file + `os.replace()`). The system supports:
- **Crash recovery**: `check_stale_running()` detects phases stuck in RUNNING status
- **Phase reset**: `reset_phase(name)` sets a single phase back to PENDING; `reset_all()` clears everything
- **Strict phase validation**: `mark_completed()`/`mark_failed()` raise `KeyError` for unknown phase names
- CLI `pipeline reset [phase]` command for manual recovery

---

## Threading Model (GUI)

```
┌──────────────────────┐          ┌──────────────────────┐
│    Main Thread        │          │  Background Thread   │
│    (tkinter event     │          │  (pipeline phase)    │
│     loop)             │          │                      │
│                       │          │                      │
│  1. User clicks       │          │                      │
│     "Run Setup"       │─────────►│  2. run_setup()      │
│                       │          │     executes          │
│  3. root.after(100ms) │          │                      │
│     polls queue +     │◄─ ─ ─ ─ │  Rich console writes │
│     buffered output   │  queue   │  to StringIO buffer  │
│                       │   Lock   │  (_LockedWriter)     │
│  4. Appends text to   │          │                      │
│     log widget        │◄─────── │  5. Puts result on   │
│     (max 3000 lines)  │          │     queue when done   │
│                       │          │                      │
│  6. Callback updates  │  Event   │  Checks cancel event │
│     UI (progress bar, │─ ─ ─ ─ ►│  for cooperative stop │
│     status, dialogs)  │          │                      │
└──────────────────────┘          └──────────────────────┘
```

The `_BufferedConsole` replaces the global Rich `console` object with one that writes to a `StringIO` via a `_LockedWriter` inner class (guarded by `threading.Lock` for thread safety). The main thread polls every 100ms for new output and appends it to the log widget (bounded at `MAX_LOG_LINES = 3000`). A `queue.Queue` carries completion/error signals from the background thread back to the main thread.

Cancellation uses a `threading.Event` (`_cancel_event`): the cancel button sets the event, and the background thread checks it at safe points. The `WM_DELETE_WINDOW` protocol handler (`_on_close()`) prevents closing the window while a phase is running.

### GUI Features

- **Step ordering**: `_STEP_DEPS` dict enforces phase prerequisites (e.g., extract requires setup)
- **Keyboard shortcuts**: Cmd/Ctrl+1-4 navigate steps, Enter runs current phase, Esc cancels, Cmd/Ctrl+F opens log search
- **Client filter**: Entry field to filter clients by name in extraction and generation steps
- **Extraction preview**: Dropdown to inspect per-client extraction results in the review step
- **API test button**: Tests Anthropic API connectivity in a background thread
- **Log search**: Search bar with highlight and next-match cycling (Cmd/Ctrl+F)
- **Log export**: Save log contents to a text file
- **Tooltips**: Hover tooltips on settings fields explaining each configuration option
- **Settings validation**: OIB (11 digits), date format (YYYY-MM-DD), path existence, API key presence
- **Bilingual labels**: All UI text in Croatian with English in parentheses
