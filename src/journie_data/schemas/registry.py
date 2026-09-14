from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from pydantic import BaseModel

from .models import CanonicalPoi, FieldEvidence, PoiSource, StagingRecord

SCHEMAS: dict[str, type[BaseModel]] = {
    "staging/v1": StagingRecord,
    "poi/v1": CanonicalPoi,
    "poi-source/v1": PoiSource,
    "field-evidence/v1": FieldEvidence,
}
MIGRATIONS: dict[tuple[str, str], Callable[[dict[str, Any]], dict[str, Any]]] = {}


def validate_rows(schema_version: str, rows: Iterable[dict[str, Any]]) -> list[BaseModel]:
    try:
        model = SCHEMAS[schema_version]
    except KeyError as exc:
        raise ValueError(f"unsupported schema version: {schema_version}") from exc
    return [model.model_validate(row) for row in rows]


def register_migration(
    source_version: str,
    target_version: str,
    migration: Callable[[dict[str, Any]], dict[str, Any]],
) -> None:
    key = (source_version, target_version)
    if key in MIGRATIONS:
        raise ValueError(f"migration already registered: {key}")
    MIGRATIONS[key] = migration


def migrate_row(row: dict[str, Any], target_version: str) -> dict[str, Any]:
    source_version = str(row.get("schema_version", ""))
    if source_version == target_version:
        return row
    try:
        migrated = MIGRATIONS[(source_version, target_version)](dict(row))
    except KeyError as exc:
        raise ValueError(f"no migration from {source_version!r} to {target_version!r}") from exc
    migrated["schema_version"] = target_version
    SCHEMAS[target_version].model_validate(migrated)
    return migrated
