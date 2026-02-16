# Automated Contract Price Adjustment System — Technical Blueprint v2

> **Status:** Proposal — awaiting approval before implementation  
> **Author:** Claude Opus 4.6 × Marko  
> **Date:** February 2026  
> **Scope:** ~90 client contract folders, Croatian-language documents, recurring annual price adjustments

---

## 1. Recommended Approach

### High-Level Architecture

A **four-phase Python CLI pipeline** with full user control at every stage:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  PHASE 0: SETUP                                                        │
│  Copy OneDrive folder → local ./data/source/                           │
│  Build file inventory & folder structure map                           │
└──────────────────────────────┬──────────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  PHASE 1: DISCOVERY & EXTRACTION                                       │
│  Scan folders → classify docs → convert .doc → extract text            │
│  → Claude API extracts structured pricing data (JSON)                  │
│  → Save per-client extraction results + generate control spreadsheet   │
└──────────────────────────────┬──────────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  PHASE 2: HUMAN REVIEW & APPROVAL                                      │
│  User opens control spreadsheet in Excel                               │
│  → Reviews extracted data, corrects errors                             │
│  → Sets new prices (absolute or % increase)                            │
│  → Marks each client as Approved / Rejected / Skip                     │
│  → Selects scope: all, specific clients, or filtered subset            │
└──────────────────────────────┬──────────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  PHASE 3: ANNEX GENERATION                                             │
│  Read approved rows from spreadsheet                                   │
│  → Convert HRK prices to EUR (1 EUR = 7.53450 HRK) where needed       │
│  → Render .docx annexes from template via docxtpl                      │
│  → Place in local output folders → user copies back to OneDrive        │
└─────────────────────────────────────────────────────────────────────────┘
```

### Why This Approach Over Alternatives

**Alternative A: MCP-server-based interactive approach** — Claude Code operates on files via MCP filesystem server, processing one client at a time conversationally. Rejected because: not repeatable, not batch-capable, human bottleneck per document, no audit trail.

**Alternative B: n8n / Make.com workflow automation** — Visual workflow connecting OneDrive API → document parser → spreadsheet → document generator. Rejected because: adds platform dependency, harder to debug document parsing edge cases, limited control over LLM extraction prompts, overkill for a CLI-driven process.

**Alternative C (selected): Standalone Python CLI pipeline** — Self-contained, runs in Claude Code or any terminal, full control at every step, JSON state for resumability, Excel for human review. Selected because: maximally transparent, reusable, debuggable, no external platform dependencies.

---

## 2. Phased Execution Plan

### Phase 0: Local Working Copy Setup

**Input:** OneDrive sync folder path  
**Output:** Local `./data/source/` mirror + `./data/inventory.json` file map

| Step | Action | Detail |
|------|--------|--------|
| 0.1 | Copy source folder | `shutil.copytree()` from OneDrive path → `./data/source/` |
| 0.2 | Scan all folders | Recursively enumerate all files per client folder |
| 0.3 | Build inventory | For each client folder: list all files with extension, size, modified date |
| 0.4 | Classify documents | Pattern-match filenames: `ugovor` = contract, `aneks`/`dodatak` = annex, `cjenik` = price list |
| 0.5 | Detect cross-format duplicates | Normalize filenames → group by stem → apply priority: .docx > .doc > .pdf |
| 0.6 | Determine document order | Sort by: (a) annex number in filename if present, (b) file modification date, (c) docx metadata date |
| 0.7 | Save inventory | Write `./data/inventory.json` with full folder/file structure |

**Checkpoint:** User reviews `inventory.json` or a summary printout to confirm folder mapping is correct.

**Key design decision — local copy:** Working on a local copy eliminates OneDrive Files-On-Demand issues (cloud-only placeholders, sync conflicts, access-denied errors from Python). The user controls when to copy generated annexes back to OneDrive.

### Phase 1: Document Parsing & Data Extraction

**Input:** `./data/inventory.json` + local document files  
**Output:** `./data/extractions/` (per-client JSON) + `./output/control_spreadsheet.xlsx`

| Step | Action | Detail |
|------|--------|--------|
| 1.1 | Convert .doc files | LibreOffice headless → .docx (saved alongside originals in `./data/converted/`) |
| 1.2 | Extract text | python-docx for .docx, pdfplumber for PDF-only clients |
| 1.3 | Prepare extraction prompts | Per document: include full text + structural markers for tables |
| 1.4 | Call Claude API (Batch) | Submit all ~90 documents via Message Batches API for 50% cost savings |
| 1.5 | Parse structured responses | Validate against Pydantic schema, flag low-confidence extractions |
| 1.6 | Detect HRK pricing | Flag any prices in HRK for conversion in Phase 3 |
| 1.7 | Save per-client JSON | `./data/extractions/{client_id}.json` |
| 1.8 | Generate control spreadsheet | Aggregate all extractions into `./output/control_spreadsheet.xlsx` |

**Checkpoint:** Spreadsheet generated — pipeline pauses for human review.

### Phase 2: Human Review & Approval

**Input:** `./output/control_spreadsheet.xlsx`  
**Output:** Same file, edited by user with approvals and new prices  

**This phase is 100% manual.** The user:
1. Opens the spreadsheet in Excel
2. Reviews extracted data per client (current prices, contract dates, document references)
3. Corrects any extraction errors directly in the spreadsheet
4. Enters new prices — either absolute values or a % increase column
5. Sets approval status per row: **Approved** / **Rejected** / **Skip** / **Needs Discussion**
6. Saves the file

**Scope control:** Only rows marked "Approved" will generate annexes. The user can process 4 clients or all 90 — the pipeline respects the approval column.

### Phase 3: Annex Document Generation

**Input:** Reviewed `control_spreadsheet.xlsx` + annex template(s)  
**Output:** `./output/annexes/{client_folder_name}/Aneks_XX.docx`

| Step | Action | Detail |
|------|--------|--------|
| 3.1 | Read approved rows | Load spreadsheet, filter to status = "Approved" |
| 3.2 | Currency conversion | Convert any HRK amounts to EUR at fixed rate 7.53450 |
| 3.3 | Prepare template data | Build per-client data dict with all variables for template |
| 3.4 | Render annexes | docxtpl renders .docx from template + data |
| 3.5 | Place in output folders | `./output/annexes/{client_folder_name}/Aneks_XX.docx` |
| 3.6 | Generate summary report | Log of all generated documents + pricing changes |

**Checkpoint:** User reviews generated annexes before manually copying to OneDrive.

---

## 3. Tool & Technology Stack

### Core Python Dependencies

| Package | Version | Phase | Purpose | Why This Over Alternatives |
|---------|---------|-------|---------|---------------------------|
| `python-docx` | 1.2.0 | 1 | .docx parsing — paragraphs, tables, metadata | Full structural access; `doc.tables` critical for pricing extraction |
| `docx2python` | ≥3.0 | 1 | Supplementary text extraction with table nesting | Preserves table structure as nested lists — useful for complex layouts |
| `pdfplumber` | 0.11.7 | 1 | PDF text + table extraction (fallback only) | Best table extraction; MIT license (vs PyMuPDF's AGPL) |
| `docxtpl` | 0.20.2 | 3 | Jinja2-based .docx generation from template | Preserves exact formatting of existing annexes; non-devs can edit templates in Word |
| `openpyxl` | ≥3.1.5 | 1,2,3 | Excel read + write for control spreadsheet | Only Python library that can both create and read back .xlsx |
| `anthropic` | latest | 1 | Claude API client for structured extraction | Native structured outputs, Batch API for 50% cost savings |
| `pydantic` | ≥2.0 | 1 | Data validation schemas for extraction results | Type-safe, auto-validates Claude API output |
| `pydantic-settings` | ≥2.12 | all | Configuration management (TOML + .env) | Typed config with environment variable override |
| `typer[all]` | ≥0.15 | all | CLI framework (includes Rich for progress bars) | Type-hint-based CLI args, auto-generated help |
| `chardet` | ≥5.0 | 1 | Encoding detection for legacy .doc text | Catches Windows-1250 encoded legacy files |
| `thefuzz` | latest | 0 | Fuzzy filename matching for duplicate detection | Handles slight filename variations across formats |

### System Dependencies

| Tool | Purpose | Installation |
|------|---------|-------------|
| LibreOffice | .doc → .docx conversion (headless mode) | `brew install --cask libreoffice` (macOS) or `apt install libreoffice-writer` (Linux) |
| Python 3.11+ | Runtime | Already available in Claude Code |

### MCP Servers (Optional, for Claude Code interactive use)

| Server | Purpose | When Useful |
|--------|---------|-------------|
| `@modelcontextprotocol/server-filesystem` | Direct file access from Claude Code | Browsing inventory, inspecting extraction results interactively |

**MCP is supplementary, not core.** The pipeline runs as a standalone Python CLI — no MCP dependency.

---

## 4. Document Parsing Strategy

### 4.1 File Format Priority Logic

```
For each client folder:
  1. Group files by normalized filename stem
  2. Within each group, select best format:
     - If .docx exists → use .docx (ignore .doc and .pdf of same file)
     - Else if .doc exists → convert to .docx via LibreOffice, then use converted .docx
     - Else if .pdf exists → use .pdf as fallback
  3. Classify each selected file as "contract" or "annex"
  4. Sort annexes by chronological order
  5. Identify the "latest valid document" (most recent annex, or main contract if no annexes)
```

### 4.2 Cross-Format Duplicate Detection

Normalize filename stems before comparison:
- Lowercase
- Strip extensions
- Collapse whitespace, underscores, hyphens
- Remove trailing copy markers like `(1)`, `- Copy`, `_v2`
- Apply fuzzy matching (thefuzz, threshold 90%) for near-matches

Example: `Ugovor o pružanju usluga.doc` and `Ugovor o pruzanju usluga.pdf` → same stem after normalization → keep .doc, skip .pdf.

### 4.3 Document Classification (Contract vs. Annex)

**Primary: Filename pattern matching (covers ~85-90% of cases)**

| Pattern (case-insensitive) | Classification |
|---------------------------|----------------|
| `ugovor` | Main contract |
| `aneks`, `anex`, `dodatak` | Annex/amendment |
| `prilog` | Attachment/appendix |
| `cjenik`, `cijena` | Price list (treat as annex) |
| `ponuda` | Offer (may contain pricing) |

**Secondary: Content-based classification via Claude API**  
For files that don't match patterns, include document type classification in the extraction prompt. Claude reads the document header and classifies it.

### 4.4 Determining "Latest Valid" Document

Chronological ordering strategy (multiple signals, in priority):

1. **Annex number in filename** — e.g., "Aneks 3" > "Aneks 2" > "Aneks 1"
2. **Date mentioned in document header** — extracted by Claude during Phase 1
3. **File modification date** — `os.path.getmtime()` (reliable on local copy)
4. **Docx core properties** — `doc.core_properties.modified` / `.created`

The extraction prompt explicitly asks Claude to identify the document date and whether it supersedes prior pricing.

### 4.5 Extracting Pricing Data from Unstructured Text

This is the hardest part. Croatian contracts embed pricing in various formats:

**Format A: Table**
```
| Usluga           | Cijena (EUR/mj) |
|------------------|-----------------|
| Hosting          | 150,00          |
| Održavanje       | 300,00          |
```

**Format B: Prose**
```
"Naknada za usluge održavanja iznosi 300,00 EUR mjesečno..."
```

**Format C: Mixed / Nested**
```
Članak 5. - Cijena
5.1. Osnovna naknada: 500,00 EUR
5.2. Dodatne usluge prema cjeniku (Prilog 1)
```

**Strategy: Feed the full document text to Claude with structural markers**

Before sending to Claude API, pre-process the extracted text:
- Mark table boundaries: `[TABLE id=1]\n...\n[/TABLE]`
- Mark heading levels: `[H1] Članak 5. - Cijena`
- Preserve original numbering: `5.1.`, `5.2.`, etc.

The extraction prompt instructs Claude to:
1. Scan the ENTIRE document before extracting anything
2. Search ALL sections including prilozi/dodaci
3. Return every pricing item found with its source section reference
4. Flag currency (EUR vs HRK) for each amount
5. Set confidence level per item

### 4.6 Croatian Character Handling

All modern libraries handle Croatian natively:
- .docx files are internally UTF-8 XML — no configuration needed
- python-docx, pdfplumber, openpyxl all output Python 3 Unicode strings
- **Critical:** Apply `unicodedata.normalize('NFC', text)` after extraction to normalize composed vs. decomposed forms (č as U+010D vs c + U+030C)
- LibreOffice .doc → .docx conversion handles Windows-1250 → UTF-8 automatically

---

## 5. HRK → EUR Currency Conversion

### Conversion Rules

Croatia adopted the euro on January 1, 2023, with the fixed conversion rate:

> **1 EUR = 7.53450 HRK**

All contracts signed before 2023 may contain HRK pricing. The system must:

1. **Detect currency during extraction** — Claude flags each price as EUR or HRK
2. **Display both in control spreadsheet** — Current price shown with currency indicator; if HRK, also show EUR equivalent
3. **Convert on generation** — All new annex prices are in EUR. If source was HRK, the annex text explicitly states the conversion.

### Conversion Logic

```python
HRK_TO_EUR_RATE = 7.53450

def hrk_to_eur(amount_hrk: float) -> float:
    """Convert HRK to EUR using official fixed rate. Round to 2 decimals."""
    return round(amount_hrk / HRK_TO_EUR_RATE, 2)
```

### How It Appears in the Control Spreadsheet

| Client | Service | Current Price | Currency | EUR Equivalent | New Price (EUR) | % Change |
|--------|---------|--------------|----------|---------------|----------------|----------|
| Klijent A | Hosting | 150,00 | EUR | 150,00 | 165,00 | +10% |
| Klijent B | Održavanje | 2.260,35 | HRK | 300,00 | 330,00 | +10% |
| Klijent C | Podrška | 500,00 | EUR | 500,00 | 550,00 | +10% |

### How It Appears in the Generated Annex

For clients with HRK contracts, the annex includes a conversion clause:

> *"Dosadašnja cijena usluge iznosila je 2.260,35 HRK mjesečno (protuvrijednost 300,00 EUR prema fiksnom tečaju konverzije 1 EUR = 7,53450 HRK). Novom cijenom usluge utvrđuje se iznos od 330,00 EUR mjesečno."*

For clients already in EUR, no conversion language is needed — just the price update.

---

## 6. Control Spreadsheet Design (Detailed)

### Sheet 1: "Pregled klijenata" (Client Overview)

**Locked columns (auto-populated, gray background):**

| Column | Header | Content |
|--------|--------|---------|
| A | Klijent (Client) | Client name from folder/extraction |
| B | Mapa (Folder) | Folder name (for traceability) |
| C | Glavni dokument (Main doc) | Filename of main contract |
| D | Datum ugovora (Contract date) | Contract signing date |
| E | Posljednji aneks (Latest annex) | Filename of most recent annex, or "—" |
| F | Datum aneksa (Annex date) | Date of latest annex, or "—" |
| G | Referentni dokument (Reference doc) | Document from which current pricing was extracted |
| H | Pouzdanost (Confidence) | Extraction confidence: Visoka/Srednja/Niska |

**Editable columns (yellow background):**

| Column | Header | Content |
|--------|--------|---------|
| I | Status | Dropdown: Odobreno / Odbijeno / Preskočeno / Za raspravu |
| J | Napomene (Notes) | Free text for reviewer comments |
| K | Datum pregleda (Review date) | Date when reviewed |

### Sheet 2: "Cijene" (Pricing)

One row per service per client (a client with 5 services = 5 rows).

**Locked columns (gray):**

| Column | Header |
|--------|--------|
| A | Klijent |
| B | Usluga (Service) |
| C | Trenutna cijena (Current price) |
| D | Valuta (Currency) — EUR or HRK |
| E | EUR protuvrijednost (EUR equivalent) — formula: if HRK, =C/7.5345; if EUR, =C |
| F | Jedinica (Unit) — mjesečno/godišnje/jednokratno/po satu |

**Editable columns (yellow):**

| Column | Header |
|--------|--------|
| G | Nova cijena EUR (New price) |
| H | % promjene (% change) — formula: =(G-E)/E |
| I | Primjena od (Effective from) — date |

### Sheet 3: "Inventar" (File Inventory)

Full file listing from Phase 0 — read-only reference for traceability.

| Column | Content |
|--------|---------|
| A | Client folder name |
| B | Filename |
| C | Extension |
| D | Size |
| E | Modified date |
| F | Classification (ugovor/aneks/prilog) |
| G | Status (selected/duplicate-skipped/ignored) |

### Spreadsheet Features

- **Freeze panes** at row 2 on all sheets (header stays visible)
- **Auto-filter** on all columns
- **Conditional formatting:** green for Odobreno, red for Odbijeno, yellow for Za raspravu
- **Data validation dropdowns** on Status column
- **Sheet protection** on locked columns (password-protected) with sorting/filtering still allowed
- **Cell comments** on header cells explaining expected input

---

## 7. Annex Template Design

### Template Strategy

1. **Extract an existing real annex** from a client folder that has good formatting
2. **Replace variable content** with Jinja2 placeholders: `{{ klijent_naziv }}`, `{{ datum }}`, etc.
3. **Save as `./templates/default/aneks_template.docx`**
4. Template is a regular .docx file — editable in Word by non-developers

### Proposed Annex Structure

```
┌─────────────────────────────────────────────────────┐
│                    [COMPANY LOGO]                     │
│                                                       │
│           ANEKS BR. {{ aneks_broj }}                  │
│     uz {{ referentni_dokument_tip }}                  │
│     {{ referentni_dokument_naziv }}                   │
│     od {{ referentni_dokument_datum }}                │
│                                                       │
│  Sklopljen dana {{ datum_aneksa }} u {{ mjesto }}     │
│                                                       │
│  između:                                              │
│                                                       │
│  1. {{ davatelj_naziv }}, OIB: {{ davatelj_oib }}     │
│     {{ davatelj_adresa }}                             │
│     (u daljnjem tekstu: Davatelj usluge)              │
│                                                       │
│  2. {{ klijent_naziv }}, OIB: {{ klijent_oib }}       │
│     {{ klijent_adresa }}                              │
│     (u daljnjem tekstu: Naručitelj)                   │
│                                                       │
│  ─────────────────────────────────────────────────    │
│                                                       │
│  Članak 1.                                            │
│  Ugovorne strane suglasno utvrđuju da se mijenja     │
│  {{ referentni_clanak }} {{ referentni_dokument }}    │
│  koji se odnosi na cijenu usluga.                     │
│                                                       │
│  Članak 2.                                            │
│  {% if valuta_konverzija %}                           │
│  Dosadašnje cijene usluga bile su izražene u HRK.     │
│  Sukladno prelasku na EUR (tečaj: 1 EUR = 7,53450    │
│  HRK), nove cijene utvrđuju se kako slijedi:          │
│  {% else %}                                           │
│  Nove cijene usluga utvrđuju se kako slijedi:         │
│  {% endif %}                                          │
│                                                       │
│  ┌──────────────────┬──────────┬──────────┐           │
│  │ Usluga           │ Dosad.   │ Nova     │           │
│  │                  │ cijena   │ cijena   │           │
│  ├──────────────────┼──────────┼──────────┤           │
│  │{%tr for s in stavke %}                  │           │
│  │ {{ s.usluga }}   │{{ s.stara_cijena }} │           │
│  │                  │{{ s.nova_cijena }}   │           │
│  │{%tr endfor %}                           │           │
│  └──────────────────┴──────────┴──────────┘           │
│                                                       │
│  Sve cijene su izražene u EUR bez PDV-a.              │
│                                                       │
│  Članak 3.                                            │
│  Ovaj Aneks stupa na snagu {{ datum_primjene }}.      │
│                                                       │
│  Članak 4.                                            │
│  Sve ostale odredbe {{ referentni_dokument }}         │
│  ostaju na snazi.                                     │
│                                                       │
│  Članak 5.                                            │
│  Ovaj Aneks sastavljen je u 2 (dva) istovjetna       │
│  primjerka, po 1 (jedan) za svaku ugovornu stranu.   │
│                                                       │
│  ZA DAVATELJA USLUGE:        ZA NARUČITELJA:          │
│                                                       │
│  ____________________        ____________________     │
│  {{ davatelj_naziv }}        {{ klijent_naziv }}      │
│                                                       │
└─────────────────────────────────────────────────────┘
```

### Template Variables

| Variable | Source | Example |
|----------|--------|---------|
| `{{ aneks_broj }}` | Auto-incremented from latest annex number | "4" |
| `{{ referentni_dokument_tip }}` | "Ugovor" or "Aneks br. X" | "Aneks br. 3" |
| `{{ referentni_dokument_naziv }}` | Full document title | "Ugovor o pružanju IT usluga" |
| `{{ referentni_dokument_datum }}` | Date of referenced doc | "15. siječnja 2024." |
| `{{ datum_aneksa }}` | Generation date | "16. veljače 2026." |
| `{{ klijent_naziv }}` | From extraction | "Tvrtka d.o.o." |
| `{{ klijent_oib }}` | From extraction | "12345678901" |
| `{{ stavke }}` | List of pricing items | Loop variable for table |
| `{{ valuta_konverzija }}` | Boolean: was source in HRK? | Controls conversion clause |

---

## 8. User Control & Scope Selection

### Granular Processing Control

The user is always in control of **what gets processed**:

**Option A: Via spreadsheet (primary method)**  
Set Status column to "Odobreno" only for clients you want to process. The generation phase reads only approved rows.

**Option B: Via CLI flags**
```bash
# Process all approved clients
pipeline generate

# Process only specific clients by folder name
pipeline generate --clients "Klijent_A,Klijent_B,Klijent_C,Klijent_D"

# Process only clients matching a pattern
pipeline generate --filter "d.o.o."

# Dry run — show what would be generated without creating files
pipeline generate --dry-run
```

**Option C: Via interactive selection**
```bash
# Interactive mode — checkbox-style selection in terminal
pipeline generate --interactive
```

### Review Before Generation

Before any files are created, the pipeline shows a summary:

```
╔══════════════════════════════════════════════════════════════╗
║  ANNEX GENERATION PREVIEW                                    ║
╠══════════════════════════════════════════════════════════════╣
║  Approved clients:  4 of 90                                  ║
║  Total annexes to generate: 4                                ║
║                                                              ║
║  Client              Services  Avg Change  Currency Conv.    ║
║  ──────────────────  ────────  ──────────  ──────────────    ║
║  Klijent A           3         +10.0%      No                ║
║  Klijent B           2         +10.0%      HRK → EUR         ║
║  Klijent C           5         +8.5%       No                ║
║  Klijent D           1         +15.0%      HRK → EUR         ║
║                                                              ║
║  Output: ./output/annexes/                                   ║
╚══════════════════════════════════════════════════════════════╝

Proceed? [y/N]:
```

---

## 9. Edge Cases & Risk Mitigation

### Document Parsing Failures

| Risk | Impact | Mitigation |
|------|--------|-----------|
| LibreOffice hangs on corrupt .doc | Pipeline blocks | 120-second subprocess timeout; skip file, log error, continue |
| PDF is scanned image, not text | No text extraction | Detect via page text length < 50 chars; flag in inventory; user manually provides data in spreadsheet |
| Document is password-protected | Cannot open | Detect via python-docx exception; flag in inventory |
| No pricing found in document | Empty extraction | Claude sets confidence to "low"; spreadsheet row flagged yellow; user manually enters prices |
| Pricing in unexpected format | Incorrect extraction | Confidence scoring + human review catches errors before generation |

### Currency & Number Parsing

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Mixed EUR/HRK in same contract | Incorrect totals | Claude extracts currency per line item, not per document |
| Croatian number format (1.000,00 vs 1,000.00) | Parsing errors | Normalize all numbers: strip dots-as-thousands, replace comma with period for float parsing |
| VAT included vs excluded ambiguity | Legal error in annex | Extraction explicitly captures VAT status; template adapts clause |
| Rounding errors in HRK→EUR conversion | Financial discrepancy | Use `decimal.Decimal` for conversion, round to 2 decimal places per official CNB rules |

### Data Integrity

| Risk | Impact | Mitigation |
|------|--------|-----------|
| User accidentally edits locked columns in spreadsheet | Corrupted source data | Sheet protection on locked columns; validation on read-back checks header integrity |
| Spreadsheet row order changed | Wrong client-data mapping | Client ID column used for lookup, not row position |
| Claude hallucinated pricing data | Incorrect prices in annex | Confidence scoring + mandatory human review of ALL prices before generation |
| Template rendering produces empty fields | Unprofessional annex | Pre-render validation checks all required fields are populated; abort if any empty |

### File System

| Risk | Impact | Mitigation |
|------|--------|-----------|
| OneDrive Files-On-Demand placeholders | Files appear but can't be read | Phase 0 copies to local directory — bypasses entirely |
| Filename encoding issues | Files not found | `pathlib.Path` handles Unicode natively; NFC normalization applied |
| Output overwrites existing annex | Data loss | Generation uses unique filenames with annex number; never overwrites existing files |

---

## 10. Reusability Design

### Running the Next Price Increase Cycle (e.g., February 2027)

The pipeline is designed for annual reuse with minimal effort:

**Step 1:** Update the local working copy
```bash
pipeline setup --source "/path/to/OneDrive/Contracts"
# Copies fresh data, detects NEW annexes (including ones you generated last year)
```

**Step 2:** Run extraction
```bash
pipeline extract
# Extracts current pricing from the LATEST valid document per client
# (which may now be last year's generated annex)
```

**Step 3:** Review and approve in the new spreadsheet
```bash
# User sets new prices, approves clients
```

**Step 4:** Generate new annexes
```bash
pipeline generate
# Annex numbering auto-increments from latest existing annex
# References the correct parent document
```

### What Makes It Reusable

- **No hardcoded data** — everything comes from document extraction + spreadsheet input
- **Auto-incrementing annex numbers** — detects existing annexes and continues sequence
- **Dynamic parent document reference** — always references the latest valid document
- **Configuration file** — `pipeline.toml` stores paths, company info, template selection
- **State isolation per run** — each run gets a timestamped state directory (`./runs/2026-02/`, `./runs/2027-02/`)
- **Template updates** — edit the .docx template in Word; no code changes needed

### Configuration File (`pipeline.toml`)

```toml
[general]
company_name = "Vaša Tvrtka d.o.o."
company_oib = "12345678901"
company_address = "Ulica 1, 10000 Zagreb"
default_location = "Zagreb"

[paths]
source = "/Users/marko/OneDrive/Contracts"
working_dir = "./data"
output_dir = "./output"
template = "./templates/default/aneks_template.docx"

[extraction]
model = "claude-sonnet-4-5-20250929"
use_batch_api = true
confidence_threshold = "medium"  # minimum confidence to auto-include in spreadsheet

[currency]
hrk_to_eur_rate = 7.53450
default_currency = "EUR"

[generation]
default_effective_date = "2026-03-01"
vat_note = "Sve cijene su izražene bez PDV-a."
```

---

## 11. Project Structure

```
doc-pipeline/
├── pyproject.toml              # Python project config, entry point
├── pipeline.toml               # Pipeline configuration
├── .env                        # ANTHROPIC_API_KEY only
├── CLAUDE.md                   # Instructions for Claude Code integration
├── README.md                   # Usage documentation
│
├── src/doc_pipeline/
│   ├── __init__.py
│   ├── cli.py                  # Typer app: setup, extract, review, generate, run-all
│   ├── config.py               # pydantic-settings: load pipeline.toml + .env
│   ├── state.py                # JSON state management per run
│   ├── models.py               # Pydantic models: ClientData, PricingItem, ExtractionResult
│   │
│   ├── phases/
│   │   ├── __init__.py
│   │   ├── setup.py            # Phase 0: copy, scan, classify, build inventory
│   │   ├── extraction.py       # Phase 1: parse docs, call Claude API, save results
│   │   ├── spreadsheet.py      # Phase 1→2: generate spreadsheet; Phase 2→3: read it back
│   │   └── generation.py       # Phase 3: render annexes from template
│   │
│   └── utils/
│       ├── __init__.py
│       ├── parsers.py          # .docx/.doc/.pdf text extraction
│       ├── croatian.py         # hr_date(), hr_number(), month names, currency conversion
│       ├── fileops.py          # File discovery, classification, dedup
│       └── progress.py         # Rich progress bar helpers
│
├── templates/
│   └── default/
│       └── aneks_template.docx # The Jinja2-tagged Word template
│
├── data/                       # Created at runtime (Phase 0)
│   ├── source/                 # Local copy of OneDrive folders
│   ├── converted/              # .doc → .docx conversions
│   ├── extractions/            # Per-client JSON extraction results
│   └── inventory.json          # File inventory
│
├── output/                     # Created at runtime
│   ├── control_spreadsheet.xlsx
│   └── annexes/
│       ├── Klijent_A/
│       │   └── Aneks_4.docx
│       └── Klijent_B/
│           └── Aneks_2.docx
│
├── runs/                       # State per execution cycle
│   └── 2026-02/
│       └── state.json
│
└── tests/
    ├── test_parsers.py
    ├── test_extraction.py
    ├── test_currency.py
    └── test_generation.py
```

---

## 12. Estimated Costs & Timeline

### Claude API Costs (Phase 1 Extraction)

| Model | 90 documents (standard) | 90 documents (Batch API, -50%) |
|-------|------------------------|-------------------------------|
| Claude Sonnet 4.5 | ~$13.50 | **~$6.75** |
| Claude Opus 4.5 | ~$22.50 | **~$11.25** |

Recommendation: Start with Sonnet 4.5. Only upgrade to Opus if extraction quality is insufficient for complex contracts.

### Implementation Effort Estimate

| Phase | Effort | Notes |
|-------|--------|-------|
| Project setup + CLI scaffold | 2-3 hours | Typer, config, project structure |
| Phase 0: File discovery + inventory | 3-4 hours | Scan, classify, dedup, copy |
| Phase 1: Document parsing + extraction | 6-8 hours | Hardest part — parsing variability, prompt engineering |
| Spreadsheet generation | 3-4 hours | openpyxl formatting, protection, validation |
| Phase 3: Annex generation | 4-5 hours | Template creation, docxtpl rendering, currency logic |
| Testing + edge case handling | 4-6 hours | Sample documents, error handling |
| **Total** | **~22-30 hours** | Split across implementation sessions |

---

## 13. What I Need to Proceed

Before implementation, I'd need:

1. **2-3 sample client folders** — ideally including:
   - One with a main contract + 1-2 annexes (.docx)
   - One with a .doc file (legacy format)
   - One with HRK pricing
   - (These can be anonymized/redacted — I just need to see the document structure and pricing format)

2. **An existing annex document** to use as the template base — the one with the formatting you want to replicate

3. **Your company details** for the annex template (company name, OIB, address) — or I can use placeholders and you fill them in later

4. **Confirmation of this plan** — or any changes you'd like before I start coding

---

*This blueprint is ready for implementation in Claude Code CLI. All phases are designed to be built and tested incrementally — Phase 0 first, then Phase 1 with a few sample documents, etc.*
