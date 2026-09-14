"""Website and Vietnamese phone normalization."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_PHONE_EXTENSION_RE = re.compile(r"(?:ext\.?|extension|x|máy lẻ)\s*\d+\s*$", re.I)


def normalize_website(value: str | None) -> str | None:
    """Normalize a URL to a lower-case hostname suitable for matching."""
    if value is None:
        return None
    candidate = str(value).strip()
    if not candidate:
        return None
    parsed = urlsplit(candidate if "://" in candidate else f"//{candidate}")
    hostname = parsed.hostname
    if not hostname or "." not in hostname:
        return None
    hostname = hostname.rstrip(".").lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    try:
        return hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return None


def normalize_phone(value: str | None, country_code: str = "VN") -> str | None:
    """Normalize a single phone number, using +84 for Vietnamese local numbers."""
    if value is None:
        return None
    candidate = _PHONE_EXTENSION_RE.sub("", str(value).strip())
    has_plus = candidate.startswith("+")
    digits = "".join(character for character in candidate if character.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
        has_plus = True
    if country_code.upper() == "VN":
        if digits.startswith("0") and 9 <= len(digits) <= 11:
            digits = "84" + digits[1:]
            has_plus = True
        elif digits.startswith("84"):
            has_plus = True
    if not 8 <= len(digits) <= 15:
        return None
    return f"+{digits}" if has_plus else digits
