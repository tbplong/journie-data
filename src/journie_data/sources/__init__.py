from .base import FetchResult, FetchStatus, RetryPolicy, SourceAdapter, ValidationReport
from .foursquare import FoursquareAdapter
from .osm import OSMAdapter
from .overture import OvertureAdapter
from .registry import SourceRegistry, register_builtin_adapters, registry
from .visithcmc import VisitHCMCAdapter

register_builtin_adapters()

__all__ = [
    "FetchResult",
    "FetchStatus",
    "FoursquareAdapter",
    "OSMAdapter",
    "OvertureAdapter",
    "RetryPolicy",
    "SourceAdapter",
    "SourceRegistry",
    "ValidationReport",
    "VisitHCMCAdapter",
    "register_builtin_adapters",
    "registry",
]
