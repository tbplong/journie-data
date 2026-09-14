"""Category-prior expected-spend estimates."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CostEstimate:
    min_vnd: int
    typical_vnd: int
    max_vnd: int
    type: str = "expected_spend"
    method: str = "heuristic"
    source: str = "journie_category_prior_v1"
    confidence: float = 0.4


def estimate_cost(
    category: str | None,
    estimated_spend: Mapping[str, Mapping[str, int]],
    *,
    source: str = "journie_category_prior_v1",
    confidence: float = 0.4,
) -> CostEstimate | None:
    """Look up a validated cost band; unknown categories remain unknown."""
    if not category or category not in estimated_spend:
        return None
    band = estimated_spend[category]
    try:
        minimum, typical, maximum = band["min"], band["typical"], band["max"]
    except KeyError as error:
        raise ValueError(f"cost mapping for {category!r} requires min, typical, and max") from error
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in (minimum, typical, maximum)
    ):
        raise ValueError(f"cost mapping for {category!r} must contain non-negative integers")
    if not minimum <= typical <= maximum:
        raise ValueError(f"cost mapping for {category!r} must satisfy min <= typical <= max")
    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be between 0 and 1")
    return CostEstimate(minimum, typical, maximum, source=source, confidence=confidence)
