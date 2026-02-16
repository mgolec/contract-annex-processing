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
    │     • User content: full document text (Croatian)
    │     • Response: JSON with client info, pricing table, confidence score
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
    │     • Sheet 1 → approved clients (status == "Odobreno")
    │     • Sheet 2 → new EUR prices per service per client
    │
    │  2. For each approved client:
    │     • Load extraction JSON
    │     • Parse source .docx for client director, address, hour fund
    │     • Build Jinja2 template context (30+ variables)
    │     • Convert HRK → EUR if needed (Decimal, rate 7.53450)
    │     • Format all values Croatian-style (dates, numbers)
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
├── models.py              All data models: FileEntry, ClientEntry, Inventory,
│                          ExtractionResult, PricingItem, ClientExtraction, enums
├── state.py               RunState — tracks phase completion per execution cycle
├── cli.py                 Typer CLI — 6 commands (setup, extract, generate, status, inventory, validate-template)
├── gui.py                 Tkinter GUI — 5-step wizard wrapping the same phase functions
├── utils/
│   ├── progress.py        Rich console, summary tables
│   ├── croatian.py        NFC normalization, hr_date(), hr_number(), month names
│   ├── fileops.py         copy_source_tree(), discover_clients(), classify_file()
│   └── parsers.py         extract_docx_text(), convert_doc_to_docx(), extract_pdf_text()
└── phases/
    ├── setup.py           Phase 0: run_setup() → Inventory
    ├── extraction.py      Phase 1: run_extraction() → list[ClientExtraction]
    ├── spreadsheet.py     Phase 1: generate_spreadsheet() — 3-sheet Excel with protection
    └── generation.py      Phase 3: run_generation() → list[Path]
                           Also: read_approved_clients(), build_context(), validate_template()
```

---

## Key Design Decisions

### 1. GUI and CLI share the same phase functions

Both `cli.py` and `gui.py` call the same `run_setup()`, `run_extraction()`, and `run_generation()` functions. The GUI adds:
- Background threading (keeps UI responsive)
- Rich console output capture (redirected to a text widget)
- Auto-confirmation (patches `console.input()` since GUI has its own dialogs)

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

All text passes through NFC Unicode normalization (composed forms for č, ć, ž, š, đ). Dates use Croatian genitive month names. Numbers use Croatian formatting (dot = thousands, comma = decimal). Currency conversion uses `decimal.Decimal` for precision.

---

## File Format Pipeline

```
.docx ──────────────────────────────────► extract_docx_text()  ──► full text with [TABLE] markers
.doc  ──► LibreOffice (headless) ──► .docx ──► extract_docx_text()
.pdf  ──────────────────────────────────► extract_pdf_text()   ──► pdfplumber page text

Deduplication: if same document exists as .docx + .doc + .pdf, only .docx is used.
```

---

## State Management

Each execution cycle is tracked in `runs/YYYY-MM/state.json`:

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

This allows resuming after interruption and tracking which phases have been completed.

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
│                       │          │                      │
│  4. Appends text to   │          │  5. Puts result on   │
│     log widget        │◄─────── │     queue when done   │
│                       │          │                      │
│  6. Callback updates  │          └──────────────────────┘
│     UI (progress bar, │
│     status, dialogs)  │
└──────────────────────┘
```

The `_BufferedConsole` replaces the global Rich `console` object with one that writes to a `StringIO`. The main thread polls every 100ms for new output and appends it to the log widget. A `queue.Queue` carries completion/error signals from the background thread back to the main thread.
