"""File discovery, classification, deduplication, and document chain building."""

from __future__ import annotations

import fcntl
import os
import re
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from rich.progress import Progress, SpinnerColumn, TextColumn

from doc_pipeline.models import (
    ClientEntry,
    ClientStatus,
    DocType,
    DocumentChain,
    FileEntry,
    FileStatus,
)
from doc_pipeline.utils.croatian import nfc

# Files to skip during copy (all lowercase for case-insensitive matching)
SKIP_FILES = {".ds_store", "thumbs.db", "desktop.ini", ".gitkeep"}


def _ignore_junk(directory: str, files: list[str]) -> set[str]:
    """Case-insensitive junk file filter for shutil.copytree."""
    return {f for f in files if f.lower() in SKIP_FILES or f.startswith('._')}

# Valid document extensions
VALID_EXTENSIONS = {".docx", ".doc", ".pdf"}

# Extension priority (lower = better)
EXT_PRIORITY = {".docx": 0, ".doc": 1, ".pdf": 2}

# Contract number pattern: U-YY-NN
CONTRACT_NUMBER_RE = re.compile(r"U-(\d{2})-(\d{2,3})", re.IGNORECASE)

# Copy/version suffixes to strip during normalization
COPY_SUFFIX_RE = re.compile(
    r"[\s_-]*(?:\(\d+\)|copy|kopija|v\d+|_v\d+|final|konacn[aoi])$",
    re.IGNORECASE,
)


# ── Classification patterns ───────────────────────────────────────────────────

# Ordered list of (pattern, DocType). First match wins.
_CLASSIFICATION_RULES: list[tuple[str, DocType]] = [
    (r"raskid", DocType.TERMINATION),
    (r"anex|aneks|dodatak", DocType.ANNEX),
    (r"ugovor\s+o\s+(?:održavanj|servisiranj|pružanj)", DocType.MAINTENANCE_CONTRACT),
    (r"povjerljivost|nda", DocType.NDA),
    (r"gdpr|obradi?\s+podataka", DocType.GDPR),
    (r"m365|office\s*365.*ugovor", DocType.M365_CONTRACT),
    (r"ponuda", DocType.OFFER),
    (r"cjenik|cijena", DocType.PRICE_LIST),
    (r"prilog", DocType.ATTACHMENT),
    (r"ugovor|cooperation\s+agreement", DocType.OTHER_CONTRACT),
]

_COMPILED_RULES = [(re.compile(p, re.IGNORECASE), dt) for p, dt in _CLASSIFICATION_RULES]


def classify_file(filename: str, extension: str) -> DocType:
    """Classify a file by its filename. Extension must include the dot."""
    if extension.lower() not in VALID_EXTENSIONS:
        return DocType.IRRELEVANT

    name_lower = nfc(filename.lower())

    for pattern, doc_type in _COMPILED_RULES:
        if pattern.search(name_lower):
            return doc_type

    # PDFs that don't match any pattern are likely scans/misc
    if extension.lower() == ".pdf":
        return DocType.IRRELEVANT

    # .doc/.docx without a matching pattern — still might be a contract
    # Default to OTHER_CONTRACT for these rather than IRRELEVANT
    return DocType.OTHER_CONTRACT


def extract_contract_number(filename: str) -> str | None:
    """Extract U-YY-NN contract number from filename. Returns e.g. 'U-21-15'."""
    m = CONTRACT_NUMBER_RE.search(filename)
    if m:
        return f"U-{m.group(1)}-{m.group(2)}"
    return None


# ── Filename normalization for dedup ──────────────────────────────────────────


def normalize_stem(filename: str) -> str:
    """Normalize a filename stem for deduplication comparison.

    Steps: strip extension → lowercase → NFC → collapse whitespace/underscores/hyphens
    → strip copy/version suffixes → strip leading/trailing whitespace.
    """
    stem = Path(filename).stem
    stem = nfc(stem.lower())
    # Collapse underscores, hyphens, multiple spaces into single space
    stem = re.sub(r"[_\-]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem)
    # Strip copy/version suffixes
    stem = COPY_SUFFIX_RE.sub("", stem)
    return stem.strip()


# ── File scanning ─────────────────────────────────────────────────────────────


def scan_folder(
    folder: Path,
    base_path: Path,
) -> list[FileEntry]:
    """Recursively scan a folder and return FileEntry objects for all files."""
    entries: list[FileEntry] = []

    for file_path in sorted(folder.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.name.lower() in SKIP_FILES:
            continue

        ext = file_path.suffix.lower()
        relative = str(file_path.relative_to(base_path))

        # Get file stats
        try:
            stat = file_path.stat()
            size = stat.st_size
            mtime = datetime.fromtimestamp(stat.st_mtime)
        except OSError:
            size = 0
            mtime = None

        doc_type = classify_file(file_path.name, ext)

        # Determine initial status
        if size == 0:
            status = FileStatus.EMPTY
        elif ext not in VALID_EXTENSIONS:
            status = FileStatus.IRRELEVANT
        elif doc_type == DocType.IRRELEVANT:
            status = FileStatus.IRRELEVANT
        else:
            status = FileStatus.SELECTED

        entry = FileEntry(
            filename=file_path.name,
            relative_path=relative,
            extension=ext,
            size_bytes=size,
            modified_date=mtime,
            doc_type=doc_type,
            status=status,
            contract_number=extract_contract_number(file_path.name),
        )

        entries.append(entry)

    return entries


# ── Cross-format deduplication ─────────────────────────────────────────────────


def dedup_files(files: list[FileEntry]) -> list[FileEntry]:
    """Mark cross-format duplicates. Within each (directory, normalized_stem) group,
    keep the best format (.docx > .doc > .pdf) and mark others as DUPLICATE_SKIPPED.
    Only considers files that are currently SELECTED.
    """
    # Group by (parent directory, normalized stem)
    groups: dict[tuple[str, str], list[FileEntry]] = defaultdict(list)
    for f in files:
        if f.status != FileStatus.SELECTED:
            continue
        parent_dir = str(Path(f.relative_path).parent)
        stem = normalize_stem(f.filename)
        groups[(parent_dir, stem)].append(f)

    # Within each group, keep best format
    for key, group in groups.items():
        if len(group) <= 1:
            continue

        # Sort by extension priority (lower = better)
        group.sort(key=lambda f: EXT_PRIORITY.get(f.extension, 99))

        # Keep the first (best format), mark rest as duplicates
        best = group[0]
        for dup in group[1:]:
            dup.status = FileStatus.DUPLICATE_SKIPPED
            dup.duplicate_of = best.relative_path

    return files


def fuzzy_dedup(files: list[FileEntry], threshold: int = 90) -> list[FileEntry]:
    """Optional fuzzy dedup pass for near-matches within same directory and doc_type.
    Uses thefuzz for string similarity. Only considers SELECTED files.
    """
    try:
        from thefuzz import fuzz
    except ImportError:
        return files

    # Group by (parent directory, doc_type)
    groups: dict[tuple[str, DocType], list[FileEntry]] = defaultdict(list)
    for f in files:
        if f.status != FileStatus.SELECTED:
            continue
        parent_dir = str(Path(f.relative_path).parent)
        groups[(parent_dir, f.doc_type)].append(f)

    for key, group in groups.items():
        if len(group) <= 1:
            continue

        # Compare all pairs
        for i, a in enumerate(group):
            if a.status != FileStatus.SELECTED:
                continue
            for b in group[i + 1 :]:
                if b.status != FileStatus.SELECTED:
                    continue
                # Never dedup files with different contract numbers
                if a.contract_number and b.contract_number and a.contract_number != b.contract_number:
                    continue
                stem_a = normalize_stem(a.filename)
                stem_b = normalize_stem(b.filename)
                if fuzz.ratio(stem_a, stem_b) >= threshold:
                    # Keep the one with better extension, or the first one
                    pri_a = EXT_PRIORITY.get(a.extension, 99)
                    pri_b = EXT_PRIORITY.get(b.extension, 99)
                    if pri_b < pri_a:
                        a.status = FileStatus.DUPLICATE_SKIPPED
                        a.duplicate_of = b.relative_path
                    else:
                        b.status = FileStatus.DUPLICATE_SKIPPED
                        b.duplicate_of = a.relative_path

    return files


# ── Document chain building ──────────────────────────────────────────────────


def _annex_sort_key(f: FileEntry) -> tuple[int, int, float]:
    """Sort key for annexes: by contract number (year, seq), then by modified date."""
    year, seq = 0, 0
    if f.contract_number:
        m = CONTRACT_NUMBER_RE.search(f.contract_number)
        if m:
            year = int(m.group(1))
            seq = int(m.group(2))
    mtime = f.modified_date.timestamp() if f.modified_date else 0
    return (year, seq, mtime)


def build_document_chain(files: list[FileEntry]) -> DocumentChain:
    """Build a document chain from a client's selected files.

    Identifies the main maintenance contract and orders annexes
    by contract number / date.
    """
    chain = DocumentChain()

    selected = [f for f in files if f.status == FileStatus.SELECTED]

    # Find main maintenance contract
    contracts = [f for f in selected if f.doc_type == DocType.MAINTENANCE_CONTRACT]
    if contracts:
        # Prefer the one without a contract number (base contract), or earliest
        base = [c for c in contracts if not c.contract_number]
        if base:
            chain.main_contract = base[0].relative_path
        else:
            contracts.sort(key=_annex_sort_key)
            chain.main_contract = contracts[0].relative_path

    # If no maintenance contract, try OTHER_CONTRACT
    if not chain.main_contract:
        other = [f for f in selected if f.doc_type == DocType.OTHER_CONTRACT]
        if other:
            chain.main_contract = other[0].relative_path

    # Find and sort annexes
    annexes = [f for f in selected if f.doc_type == DocType.ANNEX]
    annexes.sort(key=_annex_sort_key)
    chain.annexes = [a.relative_path for a in annexes]

    # Also include price lists as part of the chain
    price_lists = [f for f in selected if f.doc_type == DocType.PRICE_LIST]
    if price_lists:
        price_lists.sort(key=_annex_sort_key)
        chain.annexes.extend(p.relative_path for p in price_lists)

    # Latest valid document = most recent annex, or main contract
    if chain.annexes:
        chain.latest_valid_document = chain.annexes[-1]
    elif chain.main_contract:
        chain.latest_valid_document = chain.main_contract

    return chain


# ── Copy source tree ──────────────────────────────────────────────────────────


def copy_source_tree(
    source: Path,
    dest: Path,
    *,
    force: bool = False,
) -> tuple[int, int]:
    """Copy the source contracts tree to the working directory.

    Returns (files_copied, files_skipped).
    Handles the loose OU Nogolica file at root by creating a virtual folder.
    Uses atomic rename with rollback on --force to avoid data loss.
    """
    if dest.exists() and not force:
        raise FileExistsError(
            f"Destination already exists: {dest}\n"
            "Use --force to overwrite."
        )

    # Atomic copy with rollback when force-overwriting
    backup = dest.with_name(dest.name + "_backup")
    try:
        if dest.exists() and force:
            # Move existing to backup instead of deleting immediately
            if backup.exists():
                shutil.rmtree(backup)
            dest.rename(backup)

        dest.mkdir(parents=True, exist_ok=True)

        copied = 0
        skipped = 0

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
        ) as progress:
            task = progress.add_task("Copying files...", total=None)

            for item in sorted(source.iterdir()):
                if item.name.lower() in SKIP_FILES or item.name.startswith('._'):
                    skipped += 1
                    continue

                if item.is_dir():
                    # Copy entire client folder
                    dest_dir = dest / item.name
                    shutil.copytree(
                        item,
                        dest_dir,
                        ignore=_ignore_junk,
                    )
                    # Count files in copied tree
                    copied += sum(1 for f in dest_dir.rglob("*") if f.is_file())
                    progress.update(task, description=f"Copied: {item.name}")

                elif item.is_file() and item.suffix.lower() in VALID_EXTENSIONS:
                    # Loose file at root — create virtual folder
                    # Extract client name by removing common prefixes like "Ugovor o održavanju"
                    virtual_name = item.stem
                    name_lower = nfc(virtual_name.lower())
                    for prefix in [
                        "ugovor o održavanju ",
                        "ugovor o servisiranju ",
                        "ugovor o pružanju usluga ",
                        "ugovor ",
                    ]:
                        if name_lower.startswith(nfc(prefix)):
                            virtual_name = virtual_name[len(prefix):]
                            break
                    virtual_name = virtual_name.strip()
                    virtual_dir = dest / virtual_name
                    virtual_dir.mkdir(exist_ok=True)
                    shutil.copy2(item, virtual_dir / item.name)
                    copied += 1
                    progress.update(task, description=f"Copied loose file: {item.name}")

        # Success — remove backup if it exists
        if backup.exists():
            shutil.rmtree(backup)

    except Exception:
        # Rollback — remove partial dest, restore backup
        if dest.exists():
            shutil.rmtree(dest)
        if backup.exists():
            backup.rename(dest)
        raise

    return copied, skipped


# ── Discover clients ──────────────────────────────────────────────────────────


def discover_clients(source_dir: Path) -> list[ClientEntry]:
    """Discover all clients from the working copy directory.

    Each top-level subdirectory = one client.
    Scans recursively, classifies files, deduplicates, builds chains.
    """
    clients: list[ClientEntry] = []

    # Sort directories for deterministic ordering
    dirs = sorted(
        [d for d in source_dir.iterdir() if d.is_dir()],
        key=lambda d: nfc(d.name.lower()),
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        task = progress.add_task("Scanning clients...", total=len(dirs))

        for client_dir in dirs:
            progress.update(task, advance=1, description=f"Scanning: {client_dir.name}")

            files = scan_folder(client_dir, source_dir)
            files = dedup_files(files)
            files = fuzzy_dedup(files)

            # Determine client status
            flags: list[str] = []
            status = ClientStatus.OK

            selected = [f for f in files if f.status == FileStatus.SELECTED]
            has_relevant = any(f.extension in VALID_EXTENSIONS for f in files)

            if not files:
                status = ClientStatus.EMPTY
            elif not has_relevant and not selected:
                status = ClientStatus.NO_CONTRACT
                flags.append("no_parseable_files")
            elif not selected:
                status = ClientStatus.NO_CONTRACT

            # Check for termination
            has_termination = any(
                f.doc_type == DocType.TERMINATION and f.status == FileStatus.SELECTED
                for f in files
            )
            if has_termination:
                status = ClientStatus.TERMINATED
                flags.append("has_raskid")

            # Check if files came from subdirectories
            has_subdirs = any("/" in str(Path(f.relative_path).parent) for f in files
                             if str(Path(f.relative_path).parent) != client_dir.name)
            if has_subdirs:
                flags.append("files_in_subdirectories")

            # Check for loose root file → virtual folder
            # (the folder name matches a file stem)
            if (source_dir / (client_dir.name + ".doc")).exists() or \
               (source_dir / (client_dir.name + ".docx")).exists():
                flags.append("virtual_folder_from_root_file")

            # Build document chain
            chain = build_document_chain(files)

            # Check for maintenance contract
            has_maint = any(
                f.doc_type == DocType.MAINTENANCE_CONTRACT and f.status == FileStatus.SELECTED
                for f in files
            )
            if not has_maint and selected and status == ClientStatus.OK:
                flags.append("no_maintenance_contract")

            if flags and status == ClientStatus.OK:
                status = ClientStatus.FLAGGED

            client = ClientEntry(
                client_name=client_dir.name,
                folder_name=client_dir.name,
                folder_path=str(client_dir.relative_to(source_dir)),
                status=status,
                files=files,
                document_chain=chain,
                flags=flags,
            )
            clients.append(client)

    return clients


# ── Concurrent run protection ────────────────────────────────────────────────


class PipelineLock:
    """Simple file-based lock to prevent concurrent pipeline runs."""

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self._fd = None

    def acquire(self) -> bool:
        self._fd = open(self.lock_path, 'w')
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fd.write(str(os.getpid()))
            self._fd.flush()
            return True
        except (IOError, OSError):
            self._fd.close()
            self._fd = None
            return False

    def release(self):
        if self._fd:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            self._fd.close()
            self._fd = None
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("Another pipeline instance is already running")
        return self

    def __exit__(self, *args):
        self.release()
