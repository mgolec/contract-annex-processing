"""Pydantic v2 models for the contract pipeline."""

from __future__ import annotations

import enum
import json
import os
import unicodedata
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


# ── Enums ──────────────────────────────────────────────────────────────────────


class DocType(str, enum.Enum):
    """Document type classification based on filename patterns."""

    MAINTENANCE_CONTRACT = "maintenance_contract"
    OTHER_CONTRACT = "other_contract"
    M365_CONTRACT = "m365_contract"
    ANNEX = "annex"
    ATTACHMENT = "attachment"
    PRICE_LIST = "price_list"
    OFFER = "offer"
    NDA = "nda"
    GDPR = "gdpr"
    TERMINATION = "termination"
    IRRELEVANT = "irrelevant"


class FileStatus(str, enum.Enum):
    """Status of a file in the inventory."""

    SELECTED = "selected"
    DUPLICATE_SKIPPED = "duplicate_skipped"
    IRRELEVANT = "irrelevant"
    EMPTY = "empty"
    UNPARSEABLE = "unparseable"


class ClientStatus(str, enum.Enum):
    """Overall status of a client in the inventory."""

    OK = "ok"
    EMPTY = "empty"
    NO_CONTRACT = "no_contract"
    TERMINATED = "terminated"
    FLAGGED = "flagged"


# ── File-level models ─────────────────────────────────────────────────────────


class FileEntry(BaseModel):
    """A single file within a client folder."""

    filename: str
    relative_path: str  # relative to data/source/
    extension: str
    size_bytes: int
    modified_date: datetime | None = None
    doc_type: DocType = DocType.IRRELEVANT
    status: FileStatus = FileStatus.IRRELEVANT
    contract_number: str | None = None  # e.g. "U-21-15"
    duplicate_of: str | None = None  # relative_path of the preferred file
    flags: list[str] = Field(default_factory=list)


# ── Document chain ─────────────────────────────────────────────────────────────


class DocumentChain(BaseModel):
    """Ordered chain of contract → annexes for a client."""

    main_contract: str | None = None  # relative_path
    annexes: list[str] = Field(default_factory=list)  # ordered by number/date
    latest_valid_document: str | None = None  # most recent annex or main contract


# ── Client-level models ───────────────────────────────────────────────────────


class ClientEntry(BaseModel):
    """A single client (one top-level folder) in the inventory."""

    client_name: str
    folder_name: str
    folder_path: str  # relative to data/source/
    status: ClientStatus = ClientStatus.OK
    files: list[FileEntry] = Field(default_factory=list)
    document_chain: DocumentChain | None = None
    flags: list[str] = Field(default_factory=list)

    @field_validator('client_name', 'folder_name', mode='before')
    @classmethod
    def normalize_nfc(cls, v):
        if isinstance(v, str):
            return unicodedata.normalize('NFC', v)
        return v

    @property
    def selected_files(self) -> list[FileEntry]:
        return [f for f in self.files if f.status == FileStatus.SELECTED]

    @property
    def has_maintenance_contract(self) -> bool:
        return any(
            f.doc_type == DocType.MAINTENANCE_CONTRACT
            and f.status == FileStatus.SELECTED
            for f in self.files
        )

    @property
    def has_annexes(self) -> bool:
        return any(
            f.doc_type == DocType.ANNEX and f.status == FileStatus.SELECTED
            for f in self.files
        )


# ── Top-level inventory ───────────────────────────────────────────────────────


class Inventory(BaseModel):
    """Full file inventory produced by Phase 0."""

    # Note: created_at uses local time (CET/CEST for Croatia). No timezone conversion needed.
    created_at: datetime = Field(default_factory=datetime.now)
    source_path: str = ""
    working_path: str = ""
    clients: list[ClientEntry] = Field(default_factory=list)

    @property
    def total_clients(self) -> int:
        return len(self.clients)

    @property
    def clients_with_contracts(self) -> int:
        return sum(1 for c in self.clients if c.has_maintenance_contract)

    @property
    def clients_with_annexes(self) -> int:
        return sum(1 for c in self.clients if c.has_annexes)

    @property
    def flagged_clients(self) -> list[ClientEntry]:
        return [c for c in self.clients if c.flags or c.status != ClientStatus.OK]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        os.replace(str(tmp), str(path))

    @classmethod
    def load(cls, path: Path) -> Inventory:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


# ── Phase 1: Extraction models ──────────────────────────────────────────────


class ConfidenceLevel(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Currency(str, enum.Enum):
    EUR = "EUR"
    HRK = "HRK"


class PricingItem(BaseModel):
    """A single line item from a pricing table."""

    position: str = ""
    service_name: str = ""
    designation: str = ""
    unit: str = ""
    quantity: str = ""
    price_raw: str = ""
    price_value: Decimal | None = None
    currency: Currency = Currency.EUR
    source_section: str = ""

    @field_validator('price_value', mode='before')
    @classmethod
    def coerce_to_decimal(cls, v):
        if v is None:
            return None
        if isinstance(v, Decimal):
            return v
        return Decimal(str(v))

    @field_validator('service_name', 'designation', mode='before')
    @classmethod
    def normalize_nfc_pricing(cls, v):
        if isinstance(v, str):
            return unicodedata.normalize('NFC', v)
        return v


class ExtractionResult(BaseModel):
    """Claude API structured output — defines the tool schema for extraction."""

    client_name: str = ""
    client_oib: str = ""
    document_type: str = ""
    contract_number: str = ""
    parent_contract_number: str = ""
    document_date: str = ""
    pricing_items: list[PricingItem] = Field(default_factory=list)
    currency: Currency = Currency.EUR
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    notes: list[str] = Field(default_factory=list)
    raw_text_length: int = 0

    @field_validator('client_name', 'document_type', 'contract_number',
                     'parent_contract_number', mode='before')
    @classmethod
    def normalize_nfc_extraction(cls, v):
        if isinstance(v, str):
            return unicodedata.normalize('NFC', v)
        return v

    @field_validator('document_date', mode='before')
    @classmethod
    def validate_date_string(cls, v):
        if not v or v == "":
            return ""
        v = str(v).strip()
        # Accept various date formats - just ensure it's not garbage
        if len(v) > 50:
            return ""  # Clearly not a date
        return unicodedata.normalize('NFC', v)


class ClientExtraction(BaseModel):
    """Wrapper with pipeline metadata, saved to data/extractions/{folder}.json."""

    folder_name: str
    source_file: str
    source_extension: str
    was_converted: bool = False
    extracted_at: datetime | None = None
    extraction: ExtractionResult | None = None
    error: str | None = None

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        os.replace(str(tmp), str(path))

    @classmethod
    def load(cls, path: Path) -> ClientExtraction:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))
