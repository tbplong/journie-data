from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from journie_data.sources import (
    FoursquareAdapter,
    OSMAdapter,
    OvertureAdapter,
    VisitHCMCAdapter,
    registry,
)

FIXTURES = Path(__file__).parent / "fixtures"
RETRIEVED_AT = datetime(2026, 9, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("adapter", "fixture", "count", "first_id", "first_category"),
    [
        (OSMAdapter(), "osm.json", 2, "node/1001", "attraction.museum"),
        (OvertureAdapter(), "overture.json", 2, "ov-museum-1", "attraction.museum"),
        (FoursquareAdapter(), "foursquare.json", 1, "fsq-cafe-1", "food.cafe"),
        (VisitHCMCAdapter(), "visithcmc.json", 1, "visit-museum-1", "attraction.museum"),
    ],
)
def test_adapters_normalize_checked_in_fixtures(
    adapter: object,
    fixture: str,
    count: int,
    first_id: str,
    first_category: str,
) -> None:
    records = adapter.normalize(FIXTURES / fixture, retrieved_at=RETRIEVED_AT)

    assert len(records) == count
    assert records[0].source == adapter.source_name
    assert records[0].source_record_id == first_id
    assert records[0].category == first_category
    assert records[0].adapter_version == adapter.adapter_version
    assert records[0].retrieved_at.tzinfo is not None
    assert adapter.validate(records).valid
    assert adapter.summarize(records)["record_count"] == count


def test_osm_fixture_retains_raw_tags_and_representative_coordinates() -> None:
    records = OSMAdapter().normalize(FIXTURES / "osm.json", retrieved_at=RETRIEVED_AT)

    museum = records[0]
    assert (museum.latitude, museum.longitude) == (10.77916, 106.69979)
    assert museum.opening_hours_raw == "Mo-Su 08:00-17:00"
    assert museum.price_raw == "30000 VND"
    assert museum.raw_tags["tourism"] == "museum"


def test_local_snapshot_adapters_fetch_without_http() -> None:
    overture = OvertureAdapter(local_path=FIXTURES / "overture.json")
    foursquare = FoursquareAdapter(local_path=FIXTURES / "foursquare.json")

    assert overture.fetch({}, "2026-09-01").ok
    assert len(overture.fetch({}, "2026-09-01").records) == 2
    assert foursquare.fetch({}, "2026-09-01").ok
    assert len(foursquare.fetch({}, "2026-09-01").records) == 1


def test_overture_selects_mapped_alternate_categories() -> None:
    adapter = OvertureAdapter()
    records = adapter.normalize(
        [
            {
                "id": "mapped",
                "names": {"primary": "Museum"},
                "categories": {"primary": "education", "alternate": ["museum"]},
            },
            {
                "id": "unmapped",
                "names": {"primary": "Office"},
                "categories": {"primary": "corporate_office"},
            },
        ],
        retrieved_at=RETRIEVED_AT,
    )

    assert [(record.source_record_id, record.category) for record in records] == [
        ("mapped", "attraction.museum")
    ]


def test_osm_selects_only_mapped_key_value_categories() -> None:
    adapter = OSMAdapter()
    records = adapter.normalize(
        [
            {"type": "node", "id": 1, "tags": {"name": "Museum", "tourism": "museum"}},
            {"type": "node", "id": 2, "tags": {"name": "Office", "office": "company"}},
        ],
        retrieved_at=RETRIEVED_AT,
    )

    assert [record.source_record_id for record in records] == ["node/1"]


def test_osm_live_fetch_identifies_client_and_queries_mapped_categories() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["User-Agent"].startswith("JournieDataBot/")
        assert request.headers["Accept"] == "application/json"
        query = request.content.decode()
        assert "tourism%22%3D%22museum" in query
        assert "office" not in query
        return httpx.Response(200, json={"elements": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = OSMAdapter(client=client).fetch(
            {"bbox": {"min_lat": 10.7, "min_lon": 106.6, "max_lat": 10.8, "max_lon": 106.8}},
            "snapshot",
        )

    assert result.status.value == "empty"


def test_builtin_registry_exposes_all_milestone_adapters() -> None:
    assert registry.names() == ("foursquare", "osm", "overture", "visithcmc")
