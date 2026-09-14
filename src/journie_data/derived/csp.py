"""Strict solver-base eligibility and construction."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping

from journie_data.normalization.geo import valid_coordinates
from journie_data.parsing.opening_hours import DAYS

from ._records import dictionary, get


def _valid_hours(hours: object) -> bool:
    if not isinstance(hours, Mapping) or not all(day in hours for day in DAYS):
        return False
    found_interval = False
    for day in DAYS:
        intervals = hours[day]
        if not isinstance(intervals, list):
            return False
        for interval in intervals:
            if not isinstance(interval, (list, tuple)) or len(interval) != 2:
                return False
            if not all(
                isinstance(value, str) and len(value) == 5 and value[2] == ":" for value in interval
            ):
                return False
            found_interval = True
    return found_interval


def _cost_has_provenance(poi: object, evidence: Iterable[object] | None) -> bool:
    cost_type = get(poi, "cost_type", "unknown")
    if cost_type not in {"free", "ticket", "expected_spend"}:
        return False
    values = (get(poi, "cost_min_vnd"), get(poi, "cost_typical_vnd"), get(poi, "cost_max_vnd"))
    if not any(value is not None for value in values):
        return False
    if evidence is None:
        return True
    poi_id = get(poi, "poi_id")
    relevant = [
        item
        for item in evidence
        if get(item, "poi_id") == poi_id
        and get(item, "field") in {"cost_min_vnd", "cost_typical_vnd", "cost_max_vnd", "cost_type"}
    ]
    return any(
        get(item, "method") in {"direct", "parsed", "heuristic", "manual"} and get(item, "source")
        for item in relevant
    )


def is_csp_eligible(poi: object, evidence: Iterable[object] | None = None) -> bool:
    """Apply strict V0 solver requirements without treating unknown hours as open."""
    return not csp_ineligibility_reasons(poi, evidence)


def csp_ineligibility_reasons(poi: object, evidence: Iterable[object] | None = None) -> list[str]:
    name, category = get(poi, "name"), get(poi, "category")
    duration = get(poi, "expected_visit_duration_min")
    reasons: list[str] = []
    if not isinstance(name, str) or not name.strip():
        reasons.append("missing_name")
    if not valid_coordinates(get(poi, "latitude"), get(poi, "longitude")):
        reasons.append("missing_or_invalid_coordinates")
    if not isinstance(category, str) or not category.strip():
        reasons.append("missing_category")
    if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
        reasons.append("missing_duration")
    if not _valid_hours(get(poi, "opening_hours_normalized")):
        reasons.append("unknown_or_invalid_opening_hours")
    if not _cost_has_provenance(poi, evidence):
        reasons.append("missing_cost_with_provenance")
    if bool(get(poi, "outside_region", False)):
        reasons.append("outside_region")
    return reasons


def build_csp_record(poi: object, evidence: Iterable[object] | None = None) -> dict[str, object]:
    """Create a non-mutating CSP-base row and set its computed flag."""
    row = dictionary(poi)
    row["csp_eligible"] = is_csp_eligible(poi, evidence)
    return row


def build_csp_base(
    pois: Iterable[object],
    evidence: Iterable[object] | None = None,
) -> list[dict[str, object]]:
    """Build deterministic, eligible-only CSP rows ordered by POI ID."""
    evidence_by_poi: dict[object, list[object]] | None = None
    if evidence is not None:
        evidence_by_poi = defaultdict(list)
        for item in evidence:
            evidence_by_poi[get(item, "poi_id")].append(item)
    rows = [
        build_csp_record(
            poi,
            evidence_by_poi.get(get(poi, "poi_id"), ()) if evidence_by_poi is not None else None,
        )
        for poi in pois
    ]
    rows = [row for row in rows if row["csp_eligible"]]
    return sorted(rows, key=lambda row: str(row.get("poi_id", "")))
