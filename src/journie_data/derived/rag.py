"""Broad-coverage RAG base eligibility and construction."""

from __future__ import annotations

from collections.abc import Iterable

from journie_data.normalization.geo import valid_coordinates

from ._records import dictionary, get


def is_rag_eligible(poi: object) -> bool:
    """Apply the V0 required-field rule for retrieval POIs."""
    return not rag_ineligibility_reasons(poi)


def rag_ineligibility_reasons(poi: object) -> list[str]:
    reasons: list[str] = []
    name, category = get(poi, "name"), get(poi, "category")
    if not isinstance(name, str) or not name.strip():
        reasons.append("missing_name")
    if not valid_coordinates(get(poi, "latitude"), get(poi, "longitude")):
        reasons.append("missing_or_invalid_coordinates")
    if not isinstance(category, str) or not category.strip():
        reasons.append("missing_category")
    if bool(get(poi, "outside_region", False)):
        reasons.append("outside_region")
    return reasons


def build_rag_record(poi: object) -> dict[str, object]:
    """Create a non-mutating RAG-base row and set its computed flag."""
    row = dictionary(poi)
    row["rag_eligible"] = is_rag_eligible(poi)
    return row


def build_rag_base(pois: Iterable[object]) -> list[dict[str, object]]:
    """Build deterministic, eligible-only RAG rows ordered by POI ID."""
    rows = [build_rag_record(poi) for poi in pois]
    rows = [row for row in rows if row["rag_eligible"]]
    return sorted(rows, key=lambda row: str(row.get("poi_id", "")))
