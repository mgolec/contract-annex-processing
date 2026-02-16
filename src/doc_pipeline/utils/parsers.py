"""Document text extraction: .docx parsing with TABLE markers, .doc→.docx conversion, PDF fallback."""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

from doc_pipeline.utils.croatian import nfc


# ── LibreOffice discovery ────────────────────────────────────────────────────


def find_libreoffice() -> str | None:
    """Find the LibreOffice soffice binary. Returns path or None."""
    # Check PATH first
    soffice = shutil.which("soffice")
    if soffice:
        return soffice

    # macOS standard locations
    if platform.system() == "Darwin":
        candidates = [
            "/Applications/LibreOffice.app/Contents/MacOS/soffice",
            "/usr/local/bin/soffice",
        ]
        for c in candidates:
            if Path(c).exists():
                return c

    # Linux standard locations
    for c in ["/usr/bin/soffice", "/usr/lib/libreoffice/program/soffice"]:
        if Path(c).exists():
            return c

    return None


# ── .doc → .docx conversion ─────────────────────────────────────────────────


def convert_doc_to_docx(
    doc_path: Path,
    output_dir: Path,
    *,
    timeout: int = 120,
) -> Path | None:
    """Convert a .doc file to .docx using LibreOffice headless mode.

    Args:
        doc_path: Path to the .doc file.
        output_dir: Directory to place the converted .docx.
        timeout: Maximum seconds to wait for conversion.

    Returns:
        Path to the converted .docx file, or None on failure.
    """
    soffice = find_libreoffice()
    if not soffice:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)

    # Use a dedicated user profile to avoid conflicts with running LibreOffice
    profile_dir = Path("/tmp/lo_profile_pipeline")

    cmd = [
        soffice,
        "--headless",
        "--norestore",
        f"-env:UserInstallation=file://{profile_dir}",
        "--convert-to", "docx",
        "--outdir", str(output_dir),
        str(doc_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return None
    except (subprocess.TimeoutExpired, OSError):
        return None

    # LibreOffice names the output file with same stem + .docx
    expected = output_dir / (doc_path.stem + ".docx")
    if expected.exists() and expected.stat().st_size > 0:
        return expected

    return None


# ── .docx text extraction ───────────────────────────────────────────────────


def extract_docx_text(docx_path: Path) -> str:
    """Extract text from a .docx file with [TABLE] markers preserving document order.

    Iterates over the document body XML children directly (not doc.paragraphs)
    to preserve the interleaving of paragraphs and tables.
    """
    from docx import Document
    from docx.oxml.ns import qn

    doc = Document(str(docx_path))
    parts: list[str] = []
    table_id = 0

    for child in doc.element.body:
        tag = child.tag

        if tag == qn("w:p"):
            # Paragraph
            text = child.text or ""
            # Reconstruct full paragraph text from runs
            runs_text = []
            for r in child.iter(qn("w:t")):
                if r.text:
                    runs_text.append(r.text)
            text = "".join(runs_text).strip()

            if not text:
                continue

            # Detect heading style
            p_style = ""
            pPr = child.find(qn("w:pPr"))
            if pPr is not None:
                pStyle = pPr.find(qn("w:pStyle"))
                if pStyle is not None:
                    p_style = pStyle.get(qn("w:val"), "")

            if p_style and "heading" in p_style.lower():
                parts.append(f"[H] {text}")
            else:
                parts.append(text)

        elif tag == qn("w:tbl"):
            # Table — extract as pipe-delimited rows
            table_rows = _extract_table_from_element(child)
            if table_rows:
                parts.append(f"[TABLE id={table_id}]")
                for row in table_rows:
                    parts.append(" | ".join(cell.strip() for cell in row))
                parts.append(f"[/TABLE]")
                table_id += 1

    return nfc("\n".join(parts))


def _extract_table_from_element(tbl_element) -> list[list[str]]:
    """Extract rows/cells from a w:tbl XML element."""
    from docx.oxml.ns import qn

    rows: list[list[str]] = []
    for tr in tbl_element.iter(qn("w:tr")):
        cells: list[str] = []
        for tc in tr.iter(qn("w:tc")):
            # Get all text content within the cell
            cell_texts = []
            for p in tc.iter(qn("w:p")):
                run_texts = []
                for r in p.iter(qn("w:t")):
                    if r.text:
                        run_texts.append(r.text)
                cell_texts.append("".join(run_texts))
            cells.append(" ".join(cell_texts).strip())
        if cells:
            rows.append(cells)
    return rows


# ── PDF text extraction ─────────────────────────────────────────────────────


def extract_pdf_text(pdf_path: Path) -> str:
    """Extract text from a PDF file using pdfplumber.

    Extracts tables with [TABLE] markers and body text from each page.
    Flags scanned/image-only PDFs (< 50 chars total extracted).
    """
    import pdfplumber

    parts: list[str] = []
    table_id = 0
    total_chars = 0

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            parts.append(f"--- Page {page_num} ---")

            # Try table extraction first
            tables = page.extract_tables()
            if tables:
                for table in tables:
                    parts.append(f"[TABLE id={table_id}]")
                    for row in table:
                        if row:
                            cleaned = [str(cell).strip() if cell else "" for cell in row]
                            parts.append(" | ".join(cleaned))
                    parts.append(f"[/TABLE]")
                    table_id += 1

            # Also get body text (may overlap with table content, but Claude handles this)
            text = page.extract_text()
            if text:
                total_chars += len(text)
                parts.append(text.strip())

    result = nfc("\n".join(parts))

    # Flag if likely a scanned image
    if total_chars < 50:
        result = "[WARNING: Scanned/image PDF — minimal text extracted]\n" + result

    return result
