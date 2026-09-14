"""Conservative rule-based entity resolution."""

from .candidates import CandidatePair, generate_candidate_pairs
from .clustering import constrained_clusters
from .merge import MergeResult, merge_cluster, merge_records
from .scoring import MatchScore, score_candidate

__all__ = [
    "CandidatePair",
    "MatchScore",
    "MergeResult",
    "generate_candidate_pairs",
    "constrained_clusters",
    "merge_cluster",
    "merge_records",
    "score_candidate",
]
