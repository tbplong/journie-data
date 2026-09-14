"""Vietnamese-aware POI name normalization."""

from __future__ import annotations

import re
import unicodedata

_PUNCTUATION_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_name(value: str | None) -> str | None:
    """Return a Unicode-preserving comparison key without changing accents."""
    if value is None:
        return None
    normalized = unicodedata.normalize("NFC", str(value)).casefold()
    normalized = _PUNCTUATION_RE.sub(" ", normalized)
    normalized = _WHITESPACE_RE.sub(" ", normalized).strip()
    return normalized or None


def normalize_name_ascii(value: str | None) -> str | None:
    """Return an ASCII matching key, including Vietnamese đ/Đ handling."""
    normalized = normalize_name(value)
    if normalized is None:
        return None
    normalized = normalized.replace("đ", "d")
    decomposed = unicodedata.normalize("NFKD", normalized)
    ascii_value = decomposed.encode("ascii", "ignore").decode("ascii")
    ascii_value = _WHITESPACE_RE.sub(" ", ascii_value).strip()
    return ascii_value or None


def normalized_name_keys(value: str | None) -> tuple[str | None, str | None]:
    """Return the Unicode and ASCII keys for a display name."""
    return normalize_name(value), normalize_name_ascii(value)
