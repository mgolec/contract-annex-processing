# Contract Price Adjustment Pipeline — Claude Code Context

## What This Project Is

An automated pipeline that processes ~90 client contract folders (Croatian-language legal documents), extracts current pricing data, generates a human-review spreadsheet, and produces new annex documents (.docx) with updated prices. This is a recurring business process — the system must be reusable for future annual price adjustment cycles.

## Project Structure

```
./  (project root — you are here)
├── CLAUDE.md                          ← This file
├── PLAN.md                            ← Full technical blueprint (READ THIS FIRST)
├── pipeline.toml                      ← Pipeline configuration (create during setup)
├── .env                               ← ANTHROPIC_API_KEY (create during setup)
├── pyproject.toml                     ← Python project config
├── contracts/                         ← Source folder with all client subfolders (READ-ONLY reference)
│   ├── Klijent_A/
│   │   ├── Ugovor.docx
│   │   ├── Aneks_1.docx
│   │   └── Aneks_2.docx
│   ├── Klijent_B/
│   │   ├── Ugovor.doc
│   │   └── Ugovor.pdf              ← Duplicate of .doc — skip this
│   └── ... (~90 folders)
├── src/doc_pipeline/                  ← Pipeline source code (implement this)
├── templates/                         ← Annex .docx templates (create from real examples)
├── data/                              ← Runtime working directory (created by Phase 0)
├── output/                            ← Generated spreadsheet + annexes
└── runs/                              ← State per execution cycle
```

## Critical Rules

### READ THE PLAN FIRST
Before writing any code, read `PLAN.md` in full. It contains the complete technical blueprint with architecture decisions, library choices, edge case handling, and template design. Every implementation decision should trace back to that document.

### Implementation Order
Build and test incrementally, phase by phase. Do NOT try to build the entire pipeline at once.

1. **Phase 0 first** — file discovery, classification, inventory
2. **Phase 1 next** — document parsing, Claude API extraction, spreadsheet generation
3. **Phase 2 is manual** — user reviews spreadsheet (no code needed, but spreadsheet read-back logic is part of Phase 3)
4. **Phase 3 last** — annex generation from template

After each phase, stop and verify outputs before proceeding. Show me what was produced and ask for confirmation.

### The `contracts/` Folder Is READ-ONLY
Never modify, move, or delete anything in `./contracts/`. This is the source of truth. Phase 0 copies files to `./data/source/` for processing.

### Language & Encoding
- All contracts are in **Croatian** language
- File formats: .docx (primary), .doc (legacy), .pdf (fallback only)
- Croatian characters (č, ć, ž, š, đ) must be handled correctly everywhere
- Apply `unicodedata.normalize('NFC', text)` after any text extraction
- .docx is internally UTF-8 — no special config needed
- Legacy .doc files may use Windows-1250 encoding — LibreOffice conversion handles this

### Currency Conversion
- Older contracts may have prices in **HRK** (Croatian Kuna, pre-2023 currency)
- Fixed conversion rate: **1 EUR = 7.53450 HRK**
- ALL new annex prices must be in EUR
- Use `decimal.Decimal` for conversion calculations to avoid rounding errors
- Round to 2 decimal places per official CNB rules

### Croatian Formatting
- Dates: `12. veljače 2026.` (day. month_genitive year.)
- Numbers: `25.000,00` (period = thousands separator, comma = decimal)
- Month names (genitive): siječnja, veljače, ožujka, travnja, svibnja, lipnja, srpnja, kolovoza, rujna, listopada, studenoga, prosinca

### User Control Philosophy
- The user must approve everything before annex generation
- Only spreadsheet rows marked "Odobreno" (Approved) get processed
- Always show a preview/summary before generating files
- Always ask for confirmation before any destructive or batch operation
- Support processing a subset of clients (not always all 90)

## Technology Stack

### Python Dependencies
```
python-docx==1.2.0          # .docx parsing
docx2python>=3.0             # Supplementary structured extraction
pdfplumber==0.11.7           # PDF fallback
docxtpl==0.20.2              # Jinja2-based .docx generation
openpyxl>=3.1.5              # Excel spreadsheet (read + write)
anthropic                    # Claude API client
pydantic>=2.0                # Data schemas
pydantic-settings>=2.12.0    # Configuration (TOML + .env)
typer[all]>=0.15             # CLI framework (includes Rich)
chardet>=5.0                 # Encoding detection
thefuzz>=0.22                # Fuzzy filename matching
python-Levenshtein           # Speed up thefuzz
```

### System Dependencies
- **LibreOffice** (headless mode) for .doc → .docx conversion
- **Python 3.11+**

### Key Library Usage Notes

**python-docx**: Use `doc.paragraphs` for body text, `doc.tables` for table data, `doc.core_properties` for metadata. Tables are critical — most pricing data lives in tables.

**LibreOffice conversion**: 
```bash
soffice --headless --norestore --convert-to docx --outdir /output /input/file.doc
```
- NOT thread-safe — process files sequentially
- Use 120-second timeout via `subprocess.run(timeout=120)`
- Use dedicated user profile to avoid conflicts: `--env:UserInstallation=file:///tmp/lo_profile`

**docxtpl**: Template is a real .docx file with Jinja2 tags. Dynamic table rows use `{%tr for s in stavke %}...{%tr endfor %}` syntax. Conditional sections: `{% if valuta_konverzija %}...{% endif %}`.

**openpyxl**: Must use openpyxl (not xlsxwriter) because we need to BOTH write and read back the spreadsheet. Use sheet protection on locked columns. Use data validation for dropdown Status column.

**Claude API extraction**: Use Anthropic Python SDK with structured outputs. Batch API provides 50% cost savings for processing all documents at once. Pydantic models define the extraction schema. System prompt in English, document content in Croatian.

## File Classification Patterns

Croatian filename patterns for document classification:
- `ugovor` → Main contract
- `aneks`, `anex`, `dodatak` → Annex/amendment
- `prilog` → Attachment/appendix
- `cjenik`, `cijena` → Price list (treat as annex)
- `ponuda` → Offer

File format priority (when same document exists in multiple formats):
- .docx > .doc > .pdf
- If .docx exists, ignore .doc and .pdf of the same file
- Use .pdf ONLY when no .doc/.docx equivalent exists

## Template Creation Strategy

To create the annex template:
1. Find 2-3 real annex documents in `./contracts/` that have good formatting
2. Examine their structure, styles, fonts, layout
3. Create a copy, replace variable content with Jinja2 placeholders
4. Save as `./templates/default/aneks_template.docx`
5. The template should match the existing annex style as closely as possible

## CLI Commands to Implement

```bash
# Phase 0: Setup local working copy + build inventory
pipeline setup --source ./contracts

# Phase 1: Parse documents + extract data + generate spreadsheet  
pipeline extract [--force]  # --force to re-extract already processed docs

# Phase 3: Generate annexes from approved spreadsheet rows
pipeline generate [--clients "A,B,C"] [--dry-run] [--interactive]

# Utility commands
pipeline status              # Show current pipeline state
pipeline inventory           # Print file inventory summary
pipeline validate-template   # Check template has all required placeholders
```

## Common Pitfalls to Avoid

1. **Don't parse Croatian numbers as English** — `1.000,00` means one thousand, not `1.0`
2. **Don't assume all contracts have tables** — some have pricing in prose paragraphs
3. **Don't assume consistent document structure** — pricing sections vary across clients
4. **Don't skip the confidence scoring** — flag uncertain extractions for human review
5. **Don't hardcode company details** — everything goes in `pipeline.toml`
6. **Don't generate annexes without user confirmation** — always preview first
7. **Don't modify files in `./contracts/`** — work on copies in `./data/`
8. **Don't use xlsxwriter** — it can't read files back; use openpyxl for everything
9. **Don't process cloud-only OneDrive placeholders** — we copy to local first, so this shouldn't arise, but check file sizes > 0 as a safety measure
10. **Don't forget NFC normalization** — Croatian chars can have composed vs decomposed forms

## When You Need Clarification

If you encounter any of these, pause and ask me before proceeding:
- A contract structure you haven't seen before
- Pricing format that doesn't fit the extraction schema
- Files that can't be parsed or classified
- Ambiguity in which document is the "latest valid" one
- Any decision that affects the legal content of generated annexes
