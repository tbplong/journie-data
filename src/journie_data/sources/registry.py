from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .base import SourceAdapter

AdapterFactory = Callable[..., SourceAdapter]


class SourceRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, AdapterFactory] = {}

    def register(self, name: str, factory: AdapterFactory, *, replace: bool = False) -> None:
        key = name.strip().lower()
        if not key:
            raise ValueError("source name cannot be empty")
        if key in self._factories and not replace:
            raise ValueError(f"source adapter already registered: {key}")
        self._factories[key] = factory

    def create(self, name: str, **kwargs: Any) -> SourceAdapter:
        key = name.strip().lower()
        try:
            factory = self._factories[key]
        except KeyError as exc:
            raise KeyError(
                f"unknown source {name!r}; available: {', '.join(self.names())}"
            ) from exc
        return factory(**kwargs)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))


registry = SourceRegistry()


def register_builtin_adapters() -> SourceRegistry:
    from .foursquare import FoursquareAdapter
    from .osm import OSMAdapter
    from .overture import OvertureAdapter
    from .visithcmc import VisitHCMCAdapter

    for adapter in (OvertureAdapter, OSMAdapter, FoursquareAdapter, VisitHCMCAdapter):
        registry.register(adapter.source_name, adapter, replace=True)
    return registry
