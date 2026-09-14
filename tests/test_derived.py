from __future__ import annotations

from journie_data.derived.csp import build_csp_base
from journie_data.parsing.opening_hours import DAYS


def test_csp_base_matches_evidence_by_poi_id() -> None:
    def poi(poi_id: str) -> dict[str, object]:
        return {
            "poi_id": poi_id,
            "name": "Museum",
            "category": "attraction.museum",
            "latitude": 10.77,
            "longitude": 106.69,
            "expected_visit_duration_min": 60,
            "opening_hours_normalized": {day: [["09:00", "17:00"]] for day in DAYS},
            "cost_type": "ticket",
            "cost_typical_vnd": 100_000,
            "outside_region": False,
        }

    evidence = [
        {"poi_id": poi_id, "field": "cost_typical_vnd", "method": "direct", "source": "osm"}
        for poi_id in ("first", "second")
    ]

    rows = build_csp_base([poi("second"), poi("first")], evidence)

    assert [row["poi_id"] for row in rows] == ["first", "second"]
