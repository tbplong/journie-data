"""Read and copy mapping, dataclass, and Pydantic records without mutation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from typing import Any


def get(record: object, name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def dictionary(record: object) -> dict[str, Any]:
    if isinstance(record, Mapping):
        return dict(record)
    if hasattr(record, "model_dump"):
        return record.model_dump(mode="python")
    if is_dataclass(record):
        return asdict(record)  # type: ignore[arg-type]
    if hasattr(record, "__dict__"):
        return dict(vars(record))
    raise TypeError("record must be a mapping, dataclass, or Pydantic-style model")
