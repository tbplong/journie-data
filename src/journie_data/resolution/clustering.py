"""Precision-first constrained clustering of scored candidate pairs."""

from __future__ import annotations

from collections.abc import Sequence

from journie_data.normalization.geo import haversine_distance_m, valid_coordinates

from ._records import get, identity
from .candidates import CandidatePair
from .scoring import score_candidate


def constrained_clusters(
    records: Sequence[object],
    candidates: list[CandidatePair] | None = None,
    *,
    threshold: float = 0.90,
    max_cluster_distance_m: float = 300.0,
) -> list[list[object]]:
    """Cluster matches while forbidding same-source and geographic chain merges."""
    from .candidates import generate_candidate_pairs

    ordered = sorted(records, key=identity)
    clusters: list[list[object]] = [[record] for record in ordered]
    cluster_by_identity = {
        identity(record): cluster for record, cluster in zip(ordered, clusters, strict=True)
    }
    active_clusters = {id(cluster): cluster for cluster in clusters}
    candidates = candidates if candidates is not None else generate_candidate_pairs(ordered)
    scored = [(score_candidate(pair), pair) for pair in candidates]
    scored.sort(key=lambda item: (-item[0].score, identity(item[1].left), identity(item[1].right)))
    for score, pair in scored:
        if score.score < threshold or score.decision != "merge":
            continue
        left_id, right_id = identity(pair.left), identity(pair.right)
        left_cluster = cluster_by_identity[left_id]
        right_cluster = cluster_by_identity[right_id]
        if left_cluster is right_cluster or not _compatible(
            left_cluster, right_cluster, max_cluster_distance_m
        ):
            continue
        left_cluster.extend(right_cluster)
        left_cluster.sort(key=identity)
        for member in right_cluster:
            cluster_by_identity[identity(member)] = left_cluster
        del active_clusters[id(right_cluster)]
    return sorted(active_clusters.values(), key=lambda cluster: identity(cluster[0]))


def _compatible(left: list[object], right: list[object], max_distance_m: float) -> bool:
    left_sources = {identity(record)[0] for record in left if identity(record)[0]}
    right_sources = {identity(record)[0] for record in right if identity(record)[0]}
    if left_sources & right_sources:
        return False
    for first in left:
        for second in right:
            coordinates = (
                get(first, "latitude", "lat"),
                get(first, "longitude", "lon", "lng"),
                get(second, "latitude", "lat"),
                get(second, "longitude", "lon", "lng"),
            )
            if valid_coordinates(*coordinates[:2]) and valid_coordinates(*coordinates[2:]):
                if haversine_distance_m(*map(float, coordinates)) > max_distance_m:
                    return False
    return True
