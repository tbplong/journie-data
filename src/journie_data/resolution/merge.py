"""Canonical field resolution with model-backed provenance output."""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, is_dataclass
from dataclasses import fields as dataclass_fields
from typing import Any
from uuid import UUID, uuid5

from journie_data.normalization.contact import normalize_phone, normalize_website
from journie_data.normalization.names import normalize_name, normalize_name_ascii

from ._records import get, identity

POI_NAMESPACE = UUID("b5dc58c7-ca91-52c1-aea8-9cfcfd4f9bbc")

DEFAULT_FIELD_PRIORITY: dict[str, tuple[str, ...]] = {
    "coordinates": ("osm", "overture", "foursquare", "tourism_portal"),
    "opening_hours_raw": ("tourism_portal", "visithcmc", "osm", "overture"),
    "opening_hours_normalized": ("tourism_portal", "visithcmc", "osm", "overture"),
    "description": ("tourism_portal", "visithcmc", "overture", "osm"),
    "website": ("tourism_portal", "visithcmc", "overture", "osm", "foursquare"),
}

_FIELDS = (
    "name",
    "category",
    "address",
    "district",
    "city",
    "website",
    "phone",
    "opening_hours_raw",
    "opening_hours_normalized",
    "cost_min_vnd",
    "cost_typical_vnd",
    "cost_max_vnd",
    "cost_type",
    "expected_visit_duration_min",
    "description",
    "operating_status",
    "semantic_tags",
    "outside_region",
)


@dataclass(frozen=True, slots=True)
class MergeResult:
    poi: object
    evidence: tuple[object, ...]
    sources: tuple[object, ...]


def _model(model_type: type, values: dict[str, Any]) -> object:
    model_fields = getattr(model_type, "model_fields", None)
    if model_fields is not None:
        return model_type(**{key: value for key, value in values.items() if key in model_fields})
    if is_dataclass(model_type):
        names = {field.name for field in dataclass_fields(model_type)}
        return model_type(**{key: value for key, value in values.items() if key in names})
    parameters = inspect.signature(model_type).parameters
    accepts_kwargs = any(
        parameter.kind == parameter.VAR_KEYWORD for parameter in parameters.values()
    )
    return model_type(
        **(
            values
            if accepts_kwargs
            else {key: value for key, value in values.items() if key in parameters}
        )
    )


def _rank(record: object, field: str, priorities: dict[str, tuple[str, ...]]) -> tuple[object, ...]:
    source = identity(record)[0]
    priority = priorities.get(field, ())
    source_rank = priority.index(source) if source in priority else len(priority)
    confidence = float(get(record, "confidence", "source_confidence", default=0.0) or 0.0)
    freshness = str(get(record, "retrieved_at", "last_seen_at", default="") or "")
    reverse_freshness = "".join(chr(0x10FFFF - ord(character)) for character in freshness)
    return source_rank, -confidence, not bool(freshness), reverse_freshness, identity(record)


def _selected(
    records: list[object], field: str, priorities: dict[str, tuple[str, ...]]
) -> tuple[Any, object | None]:
    available = [(get(record, field), record) for record in records]
    if field == "cost_type":
        available = [(value, record) for value, record in available if value != "unknown"]
    if field == "website":
        available = [
            (value, record)
            for value, record in available
            if normalize_website(value) != normalize_website(get(record, "source_url"))
        ]
    available = [
        (value, record)
        for value, record in available
        if value is not None and value != "" and value != []
    ]
    if not available:
        return None, None
    value, record = min(available, key=lambda item: _rank(item[1], field, priorities))
    return value, record


def _evidence_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def merge_records(
    records: list[object],
    field_priority: dict[str, list[str] | tuple[str, ...]] | None = None,
) -> MergeResult:
    """Merge one resolved cluster into CanonicalPoi, FieldEvidence and PoiSource models."""
    if not records:
        raise ValueError("records must not be empty")
    from journie_data.schemas.models import CanonicalPoi, FieldEvidence, PoiSource

    ordered = sorted(records, key=identity)
    priorities = dict(DEFAULT_FIELD_PRIORITY)
    priorities.update({key: tuple(value) for key, value in (field_priority or {}).items()})
    identities = [f"{source}:{record_id}" for source, record_id in map(identity, ordered)]
    if any(item == ":" for item in identities):
        raise ValueError("each staging record must have source and source_record_id")
    poi_id = str(uuid5(POI_NAMESPACE, "|".join(identities)))

    canonical: dict[str, Any] = {"poi_id": poi_id}
    selected_records: dict[str, object] = {}
    for field in _FIELDS:
        value, selected_record = _selected(ordered, field, priorities)
        canonical[field] = value
        if selected_record is not None:
            selected_records[field] = selected_record
    coordinate_record = min(
        [
            record
            for record in ordered
            if get(record, "latitude", "lat") is not None
            and get(record, "longitude", "lon", "lng") is not None
        ],
        key=lambda record: _rank(record, "coordinates", priorities),
        default=None,
    )
    canonical["latitude"] = get(coordinate_record, "latitude", "lat") if coordinate_record else None
    canonical["longitude"] = (
        get(coordinate_record, "longitude", "lon", "lng") if coordinate_record else None
    )
    if coordinate_record is not None:
        selected_records["latitude"] = coordinate_record
        selected_records["longitude"] = coordinate_record
    canonical["normalized_name"] = normalize_name(canonical.get("name"))
    canonical["normalized_name_ascii"] = normalize_name_ascii(canonical.get("name"))
    canonical["normalized_website"] = normalize_website(canonical.get("website"))
    canonical["normalized_phone"] = normalize_phone(canonical.get("phone"))
    canonical["source_count"] = len({identity(record)[0] for record in ordered})
    canonical["cost_type"] = canonical.get("cost_type") or "unknown"
    canonical["semantic_tags"] = canonical.get("semantic_tags") or []
    canonical["outside_region"] = bool(canonical.get("outside_region"))
    canonical["rag_eligible"] = False
    canonical["csp_eligible"] = False
    seen = [
        get(record, "first_seen_at", "retrieved_at")
        for record in ordered
        if get(record, "first_seen_at", "retrieved_at") is not None
    ]
    latest = [
        get(record, "last_seen_at", "retrieved_at")
        for record in ordered
        if get(record, "last_seen_at", "retrieved_at") is not None
    ]
    canonical["first_seen_at"] = min(seen, key=str) if seen else None
    canonical["last_seen_at"] = max(latest, key=str) if latest else None

    evidence: list[object] = []
    evidence_fields = _FIELDS + ("latitude", "longitude", "price_raw")
    for record in ordered:
        for field in evidence_fields:
            value = get(
                record,
                field,
                *(
                    ("lat",)
                    if field == "latitude"
                    else ("lon", "lng")
                    if field == "longitude"
                    else ()
                ),
            )
            if value is None or value == "" or value == []:
                continue
            method = get(record, "method", default="direct") or "direct"
            transformation_version = None
            if field == "opening_hours_normalized" and get(record, "opening_hours_raw"):
                method = "parsed"
                transformation_version = "opening-hours/v1"
            elif field.startswith("cost_") and get(record, "price_raw"):
                method = "parsed"
                transformation_version = "price/v1"
            evidence.append(
                _model(
                    FieldEvidence,
                    {
                        "poi_id": poi_id,
                        "field": field,
                        "value_raw": _evidence_value(value),
                        "value_normalized": _evidence_value(canonical.get(field))
                        if selected_records.get(field) is record
                        else None,
                        "source": identity(record)[0],
                        "source_record_id": identity(record)[1],
                        "source_url": get(record, "source_url"),
                        "retrieved_at": get(record, "retrieved_at"),
                        "effective_from": get(record, "effective_from"),
                        "effective_to": get(record, "effective_to"),
                        "method": method,
                        "confidence": float(
                            get(record, "confidence", "source_confidence", default=0.8) or 0.8
                        ),
                        "selected": selected_records.get(field) is record,
                        "transformation_version": transformation_version,
                    },
                )
            )
    sources = tuple(
        _model(
            PoiSource,
            {
                "poi_id": poi_id,
                "source": identity(record)[0],
                "source_record_id": identity(record)[1],
                "source_url": get(record, "source_url"),
                "retrieved_at": get(record, "retrieved_at"),
                "first_seen_at": get(record, "first_seen_at", "retrieved_at"),
                "last_seen_at": get(record, "last_seen_at", "retrieved_at"),
            },
        )
        for record in ordered
    )
    return MergeResult(_model(CanonicalPoi, canonical), tuple(evidence), sources)


def merge_cluster(
    records: list[object], field_priority: dict[str, list[str] | tuple[str, ...]] | None = None
) -> MergeResult:
    return merge_records(records, field_priority)
