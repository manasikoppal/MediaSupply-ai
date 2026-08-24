"""Canonical forms used to join drug records across FDA sources."""

from __future__ import annotations

import re
import unicodedata


_LEGAL_SUFFIXES = (
    ("s", "a", "de", "c", "v"),
    ("pty", "ltd"),
    ("co", "ltd"),
    ("l", "l", "c"),
    ("l", "l", "p"),
    ("l", "p"),
    ("a", "s"),
    ("incorporated",),
    ("corporation",),
    ("company",),
    ("limited",),
    ("corp",),
    ("inc",),
    ("llc",),
    ("llp",),
    ("plc",),
    ("ltd",),
    ("gmbh",),
    ("ag",),
    ("lp",),
    ("sa",),
    ("co",),
)


def normalize_ndc(value: str | None) -> str | None:
    """Return a canonical 9-digit product or 11-digit package NDC.

    Hyphenated NDCs retain their segment meaning and are padded to the FDA
    5-4 product or 5-4-2 package representation. An unhyphenated 10-digit NDC
    is intentionally rejected because its missing zero position is ambiguous.
    """
    if value is None:
        return None
    cleaned = re.sub(r"^NDC\s*[:#]?\s*", "", str(value).strip(), flags=re.IGNORECASE)
    if not cleaned:
        return None

    if cleaned.isdigit():
        return cleaned if len(cleaned) in (9, 11) else None
    if not re.fullmatch(r"\d+(?:-\d+){1,2}", cleaned):
        return None

    parts = cleaned.split("-")
    widths = (5, 4) if len(parts) == 2 else (5, 4, 2)
    if len(parts) != len(widths) or any(not part or len(part) > width for part, width in zip(parts, widths)):
        return None
    return "".join(part.zfill(width) for part, width in zip(parts, widths))


def product_ndc_from_package(value: str | None) -> str | None:
    """Derive the canonical 9-digit product code from a package NDC."""
    normalized = normalize_ndc(value)
    if normalized and len(normalized) == 11:
        return normalized[:9]
    return normalized if normalized and len(normalized) == 9 else None


def _ascii_words(value: str) -> list[str]:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    ascii_value = ascii_value.casefold().replace("&", " and ")
    return re.findall(r"[a-z0-9]+", ascii_value)


def normalize_manufacturer(value: str | None) -> str | None:
    """Normalize punctuation, case, parent-company tags, and legal suffixes."""
    if value is None or not str(value).strip():
        return None
    cleaned = re.sub(
        r",?\s+a\s+.+?\s+company\s*$", "", str(value).strip(), flags=re.IGNORECASE
    )
    words = _ascii_words(cleaned)

    removed = True
    while words and removed:
        removed = False
        for suffix in _LEGAL_SUFFIXES:
            if tuple(words[-len(suffix) :]) == suffix:
                del words[-len(suffix) :]
                removed = True
                break
    return " ".join(words) or None


def normalize_drug_name(value: str | None) -> str | None:
    """Return a comparison-friendly generic, brand, or ingredient name."""
    if value is None or not str(value).strip():
        return None
    words = _ascii_words(str(value))
    return " ".join(words) or None
