"""Category-prior visit duration estimates."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DurationEstimate:
    value: int
    method: str = "heuristic"
    source: str = "journie_category_prior_v1"
    confidence: float = 0.5


def estimate_duration(
    category: str | None,
    duration_minutes: Mapping[str, int],
    *,
    source: str = "journie_category_prior_v1",
    confidence: float = 0.5,
) -> DurationEstimate | None:
    """Look up a positive duration; never supply an implicit default."""
    if not category or category not in duration_minutes:
        return None
    value = duration_minutes[category]
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"duration for {category!r} must be a positive integer")
    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be between 0 and 1")
    return DurationEstimate(value, source=source, confidence=confidence)
