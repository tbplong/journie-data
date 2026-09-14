from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "v1"


class RecordModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StagingRecord(RecordModel):
    schema_version: Literal["staging/v1"] = "staging/v1"
    taxonomy_version: Literal["journie-taxonomy/v1"] = "journie-taxonomy/v1"
    adapter_version: str = "v1"
    source: str
    source_record_id: str
    source_snapshot_id: str | None = None
    source_url: str | None = None
    retrieved_at: datetime
    source_updated_at: datetime | str | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    name: str
    name_en: str | None = None
    normalized_name: str | None = None
    normalized_name_ascii: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    outside_region: bool = False
    address: str | None = None
    district: str | None = None
    city: str | None = None
    website: str | None = None
    website_domain: str | None = None
    phone: str | None = None
    normalized_phone: str | None = None
    category: str | None = None
    source_categories: list[str] = Field(default_factory=list)
    opening_hours_raw: str | None = None
    opening_hours_normalized: dict[str, list[list[str]]] | None = None
    price_raw: str | None = None
    cost_min_vnd: int | None = None
    cost_typical_vnd: int | None = None
    cost_max_vnd: int | None = None
    cost_type: Literal["ticket", "expected_spend", "free", "unknown"] = "unknown"
    expected_visit_duration_min: int | None = None
    description: str | None = None
    operating_status: str | None = None
    wikidata: str | None = None
    wikipedia: str | None = None
    content_hash: str | None = None
    confidence: float = 0.8
    method: Literal["direct", "parsed", "heuristic", "llm_generated", "manual"] = "direct"
    raw_tags: dict[str, Any] = Field(default_factory=dict)
    raw_data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    @field_validator("latitude")
    @classmethod
    def valid_latitude(cls, value: float | None) -> float | None:
        if value is not None and not -90 <= value <= 90:
            raise ValueError("latitude must be in [-90, 90]")
        return value

    @field_validator("longitude")
    @classmethod
    def valid_longitude(cls, value: float | None) -> float | None:
        if value is not None and not -180 <= value <= 180:
            raise ValueError("longitude must be in [-180, 180]")
        return value


class CanonicalPoi(RecordModel):
    schema_version: Literal["poi/v1"] = "poi/v1"
    taxonomy_version: Literal["journie-taxonomy/v1"] = "journie-taxonomy/v1"
    resolver_version: Literal["entity-resolver/v1"] = "entity-resolver/v1"
    eligibility_version: Literal["eligibility/v1"] = "eligibility/v1"
    poi_id: str
    name: str
    normalized_name: str
    normalized_name_ascii: str | None = None
    category: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    outside_region: bool = False
    address: str | None = None
    district: str | None = None
    city: str | None = None
    website: str | None = None
    normalized_website: str | None = None
    phone: str | None = None
    normalized_phone: str | None = None
    opening_hours_raw: str | None = None
    opening_hours_normalized: dict[str, list[list[str]]] | None = None
    cost_min_vnd: int | None = None
    cost_typical_vnd: int | None = None
    cost_max_vnd: int | None = None
    cost_type: Literal["ticket", "expected_spend", "free", "unknown"] = "unknown"
    expected_visit_duration_min: int | None = None
    description: str | None = None
    operating_status: str | None = None
    semantic_tags: list[str] = Field(default_factory=list)
    source_count: int = 1
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    rag_eligible: bool = False
    csp_eligible: bool = False
    rag_ineligibility_reasons: list[str] = Field(default_factory=list)
    csp_ineligibility_reasons: list[str] = Field(default_factory=list)


class PoiSource(RecordModel):
    schema_version: Literal["poi-source/v1"] = "poi-source/v1"
    poi_id: str
    source: str
    source_record_id: str
    source_url: str | None = None
    retrieved_at: datetime | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None


class FieldEvidence(RecordModel):
    schema_version: Literal["field-evidence/v1"] = "field-evidence/v1"
    poi_id: str
    field: str
    value_raw: str | int | float | bool | None = None
    value_normalized: str | int | float | bool | None = None
    source: str
    source_record_id: str
    source_url: str | None = None
    retrieved_at: datetime | None = None
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    method: Literal["direct", "parsed", "heuristic", "llm_generated", "manual"]
    confidence: float = Field(ge=0, le=1)
    selected: bool = False
    transformation_version: str | None = None
