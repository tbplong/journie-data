"""Conservative Vietnamese-price parsing."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Price:
    min_vnd: int | None
    typical_vnd: int | None
    max_vnd: int | None
    type: str

    def as_dict(self) -> dict[str, int | str | None]:
        return {
            "min_vnd": self.min_vnd,
            "typical_vnd": self.typical_vnd,
            "max_vnd": self.max_vnd,
            "type": self.type,
        }

    def __getitem__(self, field: str) -> int | str | None:
        return self.as_dict()[field]


def _plain(value: str) -> str:
    value = value.casefold().replace("đ", "d")
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))


def _amount(token: str, suffix: str) -> int | None:
    compact = token.strip().replace(" ", "")
    if suffix.casefold() in {"k", "nghin", "ngan"}:
        try:
            return int(round(float(compact.replace(",", ".")) * 1000))
        except ValueError:
            return None
    separators = [character for character in compact if character in ".,"]
    if separators and any(len(group) != 3 for group in re.split(r"[.,]", compact)[1:]):
        return None
    digits = re.sub(r"[.,]", "", compact)
    return int(digits) if digits.isdigit() else None


def parse_price(value: str | None, price_type: str | None = None) -> Price:
    """Parse free, exact, or explicit VND ranges; ambiguous input stays unknown."""
    if value is None or not str(value).strip():
        return Price(None, None, None, "unknown")
    plain = _plain(str(value).strip())
    if re.search(r"\b(mien phi|free|khong thu phi)\b", plain):
        return Price(0, 0, 0, "free")
    if any(currency in plain for currency in ("usd", "$", "eur")):
        return Price(None, None, None, "unknown")

    matches = re.findall(r"(?<!\d)(\d+(?:[.,]\d+)?)\s*(k|nghin|ngan|vnd|vnđ|dong|d)?", plain)
    parsed_amounts = [
        _amount(number, suffix)
        for number, suffix in matches
        if suffix or "." in number or "," in number
    ]
    amounts: list[int] = [amount for amount in parsed_amounts if amount is not None]
    if not amounts or len(amounts) > 2:
        return Price(None, None, None, "unknown")
    inferred_type = price_type or "ticket"
    if inferred_type not in {"ticket", "expected_spend"}:
        raise ValueError("price_type must be 'ticket' or 'expected_spend'")
    if len(amounts) == 1:
        return Price(amounts[0], amounts[0], amounts[0], inferred_type)
    if not re.search(r"[-–]|\b(?:den|to)\b", plain):
        return Price(None, None, None, "unknown")
    low, high = sorted(amounts)
    return Price(low, None, high, inferred_type)
