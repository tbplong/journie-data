"""Explainable entity-match scoring."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from journie_data.normalization.geo import haversine_distance_m, valid_coordinates
from journie_data.normalization.names import normalize_name_ascii

from ._records import get, strong_identifiers
from .candidates import CandidatePair


@dataclass(frozen=True, slots=True)
class MatchScore:
    score: float
    name_similarity: float
    geo_similarity: float
    category_similarity: float
    strong_match: bool
    decision: str


def name_similarity(left: object, right: object) -> float:
    left_key = get(left, "normalized_name_ascii") or normalize_name_ascii(get(left, "name"))
    right_key = get(right, "normalized_name_ascii") or normalize_name_ascii(get(right, "name"))
    if not left_key or not right_key:
        return 0.0
    sequence = SequenceMatcher(None, left_key, right_key, autojunk=False).ratio()
    left_tokens, right_tokens = set(left_key.split()), set(right_key.split())
    token_score = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    return max(sequence, token_score)


def category_similarity(left: object, right: object) -> float:
    left_category, right_category = get(left, "category"), get(right, "category")
    if not left_category or not right_category:
        return 0.0
    if left_category == right_category:
        return 1.0
    return (
        0.5 if str(left_category).split(".", 1)[0] == str(right_category).split(".", 1)[0] else 0.0
    )


def score_candidate(
    pair_or_left: CandidatePair | object,
    right: object | None = None,
    *,
    max_distance_m: float = 150.0,
    auto_merge_threshold: float = 0.90,
    review_threshold: float = 0.75,
    contradiction_distance_m: float = 1_000.0,
) -> MatchScore:
    """Score a pair and classify it as merge, review, or separate."""
    if isinstance(pair_or_left, CandidatePair):
        pair = pair_or_left
    else:
        if right is None:
            raise TypeError("right record is required")
        left_lat, left_lon = (
            get(pair_or_left, "latitude", "lat"),
            get(pair_or_left, "longitude", "lon", "lng"),
        )
        right_lat, right_lon = get(right, "latitude", "lat"), get(right, "longitude", "lon", "lng")
        distance = None
        if valid_coordinates(left_lat, left_lon) and valid_coordinates(right_lat, right_lon):
            distance = haversine_distance_m(
                float(left_lat), float(left_lon), float(right_lat), float(right_lon)
            )
        shared = tuple(sorted(strong_identifiers(pair_or_left) & strong_identifiers(right)))
        pair = CandidatePair(pair_or_left, right, distance, shared)
    names = name_similarity(pair.left, pair.right)
    categories = category_similarity(pair.left, pair.right)
    geo = 0.0 if pair.distance_m is None else max(0.0, 1.0 - pair.distance_m / max_distance_m)
    strong = bool(pair.shared_identifiers)
    contradictory = pair.distance_m is not None and pair.distance_m > contradiction_distance_m
    score = 0.55 * names + 0.30 * geo + 0.15 * categories
    if strong and not contradictory:
        score = 1.0
    decision = (
        "merge"
        if score >= auto_merge_threshold and not contradictory
        else "review"
        if score >= review_threshold
        else "separate"
    )
    return MatchScore(round(score, 6), names, geo, categories, strong, decision)
