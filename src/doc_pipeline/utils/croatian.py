"""Croatian language utilities: NFC normalization, date formatting, month names."""

from __future__ import annotations

import unicodedata
from datetime import date, datetime


# Genitive case month names used in Croatian date formatting
MONTHS_GENITIVE = [
    "",  # 0-indexed placeholder
    "siječnja",
    "veljače",
    "ožujka",
    "travnja",
    "svibnja",
    "lipnja",
    "srpnja",
    "kolovoza",
    "rujna",
    "listopada",
    "studenoga",
    "prosinca",
]

MONTHS_NOMINATIVE = [
    "",
    "siječanj",
    "veljača",
    "ožujak",
    "travanj",
    "svibanj",
    "lipanj",
    "srpanj",
    "kolovoz",
    "rujan",
    "listopad",
    "studeni",
    "prosinac",
]


def nfc(text: str) -> str:
    """Apply NFC Unicode normalization (Croatian composed characters)."""
    return unicodedata.normalize("NFC", text)


def hr_date(d: date | datetime) -> str:
    """Format a date in Croatian style: '16. veljače 2026.'"""
    if isinstance(d, datetime):
        d = d.date()
    return f"{d.day}. {MONTHS_GENITIVE[d.month]} {d.year}."


def hr_number(value: float, decimals: int = 2) -> str:
    """Format a number in Croatian style: '25.000,00' (dot=thousands, comma=decimal)."""
    formatted = f"{value:,.{decimals}f}"
    # Swap separators: English (1,000.00) → Croatian (1.000,00)
    # Step 1: comma → temp, Step 2: dot → comma, Step 3: temp → dot
    formatted = formatted.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return formatted


def parse_hr_number(text: str) -> float | None:
    """Parse a Croatian-formatted number: '25.000,00' → 25000.0.
    Returns None if parsing fails.
    """
    text = text.strip()
    if not text:
        return None
    try:
        # Remove dots (thousands separator), replace comma with dot (decimal)
        cleaned = text.replace(".", "").replace(",", ".")
        return float(cleaned)
    except ValueError:
        return None
