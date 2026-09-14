from __future__ import annotations

from datetime import UTC, datetime

from journie_data.resolution import (
    constrained_clusters,
    generate_candidate_pairs,
    merge_records,
    score_candidate,
)

NOW = datetime(2026, 9, 1, tzinfo=UTC)


def record(source: str, record_id: str, **updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "source": source,
        "source_record_id": record_id,
        "name": "Bảo tàng Thành phố Hồ Chí Minh",
        "category": "attraction.museum",
        "latitude": 10.77916,
        "longitude": 106.69979,
        "retrieved_at": NOW,
        "confidence": 0.8,
        "method": "direct",
    }
    value.update(updates)
    return value


def test_candidates_are_cross_source_and_strong_ids_can_bridge_missing_geo() -> None:
    osm = record("osm", "1", website="https://museum.example/place")
    overture = record("overture", "2", latitude=None, longitude=None, website="museum.example")
    duplicate_osm = record("osm", "3")

    pairs = generate_candidate_pairs([duplicate_osm, overture, osm])

    assert len(pairs) == 1
    strong_pair = pairs[0]
    assert strong_pair.shared_identifiers == (("website", "museum.example"),)
    assert score_candidate(strong_pair).decision == "merge"


def test_candidates_find_nearby_records_across_latitude_windows() -> None:
    records = [
        record("osm", "1", latitude=10.0, longitude=106.0),
        record("overture", "2", latitude=10.001, longitude=106.0),
        record("visithcmc", "3", latitude=10.01, longitude=106.0),
    ]

    pairs = generate_candidate_pairs(records)

    assert [(pair.left["source"], pair.right["source"]) for pair in pairs] == [
        ("osm", "overture")
    ]
    assert pairs[0].distance_m is not None and pairs[0].distance_m < 150


def test_repeated_or_multi_tenant_websites_are_not_strong_identifiers() -> None:
    records = [
        record("osm", "1", latitude=None, longitude=None, website="facebook.com/place-one"),
        record("overture", "2", latitude=None, longitude=None, website="facebook.com/place-two"),
        record("osm", "3", latitude=None, longitude=None, website="chain.example/one"),
        record("osm", "4", latitude=None, longitude=None, website="chain.example/two"),
        record("overture", "5", latitude=None, longitude=None, website="chain.example/three"),
    ]

    assert generate_candidate_pairs(records) == []


def test_candidates_do_not_compare_large_same_source_groups() -> None:
    records = [
        record("overture", str(index), latitude=10 + index / 1_000_000)
        for index in range(500)
    ]

    assert generate_candidate_pairs(records) == []


def test_strong_identifier_does_not_override_clear_geographic_contradiction() -> None:
    left = record("osm", "1", website="museum.example")
    right = record(
        "overture", "2", latitude=10.80, longitude=106.72, website="https://museum.example"
    )

    pair = generate_candidate_pairs([left, right])[0]
    score = score_candidate(pair)

    assert score.strong_match
    assert score.decision == "separate"


def test_constrained_clustering_does_not_chain_same_source_branches() -> None:
    first = record("osm", "1", website="same.example")
    second = record("overture", "2", website="same.example")
    third = record("osm", "3", website="same.example")

    clusters = constrained_clusters([first, second, third])

    assert sorted(len(cluster) for cluster in clusters) == [1, 2]
    assert all(len({item["source"] for item in cluster}) == len(cluster) for cluster in clusters)


def test_merge_obeys_field_priority_and_records_selected_provenance() -> None:
    osm = record(
        "osm",
        "node/1",
        name="Museum from OSM",
        description="short",
        website="https://museum.example/osm",
        source_url="https://openstreetmap.org/node/1",
    )
    visit = record(
        "visithcmc",
        "visit-1",
        name="Museum from portal",
        description="curated description",
        website="https://museum.example",
        source_url="https://portal.example/museum",
        latitude=10.77917,
        longitude=106.69980,
    )

    merged = merge_records(
        [visit, osm],
        {
            "name": ["visithcmc", "osm"],
            "description": ["visithcmc", "osm"],
            "coordinates": ["osm", "visithcmc"],
        },
    )

    assert merged.poi.name == "Museum from portal"
    assert merged.poi.description == "curated description"
    assert (merged.poi.latitude, merged.poi.longitude) == (10.77916, 106.69979)
    assert merged.poi.source_count == 2
    assert {(item.source, item.source_record_id) for item in merged.sources} == {
        ("osm", "node/1"),
        ("visithcmc", "visit-1"),
    }
    selected_name = [item for item in merged.evidence if item.field == "name" and item.selected]
    assert len(selected_name) == 1
    assert selected_name[0].source == "visithcmc"
    assert selected_name[0].value_normalized == "Museum from portal"


def test_canonical_id_is_stable_independent_of_input_order() -> None:
    left = record("osm", "1")
    right = record("overture", "2")

    assert merge_records([left, right]).poi.poi_id == merge_records([right, left]).poi.poi_id


def test_canonical_name_allows_no_ascii_equivalent() -> None:
    merged = merge_records([record("overture", "1", name="永長寺")])

    assert merged.poi.normalized_name == "永長寺"
    assert merged.poi.normalized_name_ascii is None
