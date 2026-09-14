"""Geographic and strong-identifier candidate generation."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations
from math import degrees

from journie_data.normalization.geo import EARTH_RADIUS_M, haversine_distance_m, valid_coordinates

from ._records import get, identity, strong_identifiers


@dataclass(frozen=True, slots=True)
class CandidatePair:
    left: object
    right: object
    distance_m: float | None
    shared_identifiers: tuple[tuple[str, str], ...] = ()

    def __iter__(self):
        yield self.left
        yield self.right


def generate_candidate_pairs(
    records: Sequence[object], max_distance_m: float = 150.0
) -> list[CandidatePair]:
    """Generate nearby or identifier-linked cross-source record pairs."""
    if max_distance_m <= 0:
        raise ValueError("max_distance_m must be positive")
    ordered = sorted(records, key=identity)
    identities = [identity(record) for record in ordered]
    identifier_sets = [strong_identifiers(record) for record in ordered]
    candidate_indices: set[tuple[int, int]] = set()

    identifier_index: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, identifiers in enumerate(identifier_sets):
        for identifier in identifiers:
            identifier_index[identifier].append(index)
    unique_identifiers: set[tuple[str, str]] = set()
    for identifier, indexes in identifier_index.items():
        source_counts = Counter(identities[index][0] for index in indexes)
        if len(source_counts) < 2 or any(count > 1 for count in source_counts.values()):
            continue
        unique_identifiers.add(identifier)
        for left_index, right_index in combinations(indexes, 2):
            if identities[left_index][0] != identities[right_index][0]:
                candidate_indices.add((left_index, right_index))

    source_indexes: dict[str, list[int]] = defaultdict(list)
    for index, (source, _) in enumerate(identities):
        source_indexes[source].append(index)
    latitude_window = degrees(max_distance_m / EARTH_RADIUS_M)
    for left_source, right_source in combinations(sorted(source_indexes), 2):
        right_by_latitude = sorted(
            (float(get(ordered[index], "latitude", "lat")), index)
            for index in source_indexes[right_source]
            if valid_coordinates(
                get(ordered[index], "latitude", "lat"),
                get(ordered[index], "longitude", "lon", "lng"),
            )
        )
        right_latitudes = [latitude for latitude, _ in right_by_latitude]
        for left_index in source_indexes[left_source]:
            left = ordered[left_index]
            left_latitude = get(left, "latitude", "lat")
            left_longitude = get(left, "longitude", "lon", "lng")
            if not valid_coordinates(left_latitude, left_longitude):
                continue
            latitude = float(left_latitude)
            start = bisect_left(right_latitudes, latitude - latitude_window)
            end = bisect_right(right_latitudes, latitude + latitude_window)
            for _, right_index in right_by_latitude[start:end]:
                right = ordered[right_index]
                distance = haversine_distance_m(
                    latitude,
                    float(left_longitude),
                    float(get(right, "latitude", "lat")),
                    float(get(right, "longitude", "lon", "lng")),
                )
                if distance <= max_distance_m:
                    candidate_indices.add((left_index, right_index))

    result: list[CandidatePair] = []
    for left_index, right_index in sorted(candidate_indices):
        left, right = ordered[left_index], ordered[right_index]
        left_latitude = get(left, "latitude", "lat")
        left_longitude = get(left, "longitude", "lon", "lng")
        right_latitude = get(right, "latitude", "lat")
        right_longitude = get(right, "longitude", "lon", "lng")
        candidate_distance = None
        if valid_coordinates(left_latitude, left_longitude) and valid_coordinates(
            right_latitude, right_longitude
        ):
            candidate_distance = haversine_distance_m(
                float(left_latitude),
                float(left_longitude),
                float(right_latitude),
                float(right_longitude),
            )
        shared = tuple(
            sorted(identifier_sets[left_index] & identifier_sets[right_index] & unique_identifiers)
        )
        result.append(CandidatePair(left, right, candidate_distance, shared))
    return result
