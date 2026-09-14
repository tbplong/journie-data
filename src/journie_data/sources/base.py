from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from journie_data.schemas.models import StagingRecord


class FetchStatus(StrEnum):
    SUCCESS = "success"
    EMPTY = "empty"
    NOT_CONFIGURED = "not_configured"
    UNSUPPORTED = "unsupported"
    DEFERRED = "deferred"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(slots=True)
class FetchResult:
    source: str
    status: FetchStatus
    records: list[dict[str, Any]] = field(default_factory=list)
    raw_path: Path | None = None
    discovered_urls: list[str] = field(default_factory=list)
    message: str | None = None
    http_attempts: int = 0

    @property
    def ok(self) -> bool:
        return self.status in {FetchStatus.SUCCESS, FetchStatus.EMPTY}


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 4
    backoff_seconds: float = 0.5
    max_backoff_seconds: float = 4.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.max_attempts > 10:
            raise ValueError("max_attempts must be between 1 and 10")
        if self.backoff_seconds < 0 or self.max_backoff_seconds < 0:
            raise ValueError("retry delays cannot be negative")


@dataclass(slots=True)
class ValidationReport:
    valid: bool
    record_count: int
    errors: list[str] = field(default_factory=list)


class SourceAdapter(ABC):
    source_name: str
    adapter_version: str = "v1"

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.client = client
        self.retry_policy = retry_policy or RetryPolicy()

    @abstractmethod
    def fetch(self, region: Mapping[str, Any] | Any, snapshot_date: date | str) -> FetchResult:
        """Fetch or locate raw source records without writing pipeline outputs."""

    def normalize(
        self,
        raw: Path | str | Mapping[str, Any] | Iterable[Mapping[str, Any]],
        *,
        retrieved_at: datetime | None = None,
    ) -> list[StagingRecord]:
        timestamp = retrieved_at or datetime.now(UTC)
        return [
            self.normalize_record(item, retrieved_at=timestamp)
            for item in self.select_records(read_records(raw))
            if self.include_record(item)
        ]

    def select_records(
        self, records: Iterable[Mapping[str, Any]]
    ) -> list[Mapping[str, Any]]:
        """Select source records that belong to the pipeline's domain."""
        return list(records)

    def include_record(self, raw: Mapping[str, Any]) -> bool:
        return True

    @abstractmethod
    def normalize_record(self, raw: Mapping[str, Any], *, retrieved_at: datetime) -> StagingRecord:
        """Normalize one fixture or source record into the shared contract."""

    def validate(self, records: Iterable[StagingRecord]) -> ValidationReport:
        errors: list[str] = []
        count = 0
        for index, record in enumerate(records):
            count += 1
            data = _model_dump(record)
            name = data.get("name")
            if not isinstance(name, str) or not name.strip():
                errors.append(f"record {index}: missing name")
            lat, lon = data.get("latitude"), data.get("longitude")
            if lat is not None and not -90 <= float(lat) <= 90:
                errors.append(f"record {index}: latitude out of range")
            if lon is not None and not -180 <= float(lon) <= 180:
                errors.append(f"record {index}: longitude out of range")
        return ValidationReport(not errors, count, errors)

    def summarize(self, records: Iterable[StagingRecord]) -> dict[str, Any]:
        rows = [_model_dump(record) for record in records]
        total = len(rows)

        def coverage(field_name: str) -> float:
            if not total:
                return 0.0
            return sum(value_present(row.get(field_name)) for row in rows) / total

        categories = Counter(
            str(row["category"]) for row in rows if value_present(row.get("category"))
        )
        return {
            "source": self.source_name,
            "record_count": total,
            "category_counts": dict(sorted(categories.items())),
            "name_coverage": coverage("name"),
            "address_coverage": coverage("address"),
            "website_coverage": coverage("website"),
            "opening_hours_coverage": coverage("opening_hours_raw"),
            "price_coverage": coverage("price_raw"),
            "operating_status_coverage": coverage("operating_status"),
        }

    def _request(self, method: str, url: str, **kwargs: Any) -> tuple[httpx.Response, int]:
        if self.client is None:
            raise RuntimeError(f"{self.source_name}: HTTP client is not configured")

        response: httpx.Response | None = None
        for attempt in range(1, self.retry_policy.max_attempts + 1):
            try:
                response = self.client.request(method, url, **kwargs)
                if response.status_code != 429 and response.status_code < 500:
                    response.raise_for_status()
                    return response, attempt
                response.raise_for_status()
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError):
                retryable = (
                    response is None or response.status_code == 429 or response.status_code >= 500
                )
                if not retryable or attempt == self.retry_policy.max_attempts:
                    raise
                delay = min(
                    self.retry_policy.backoff_seconds * (2 ** (attempt - 1)),
                    self.retry_policy.max_backoff_seconds,
                )
                if response is not None and response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        delay = min(float(retry_after), self.retry_policy.max_backoff_seconds)
                if delay:
                    time.sleep(delay)
        raise RuntimeError("unreachable retry state")

    def _staging_record(self, payload: Mapping[str, Any]) -> StagingRecord:
        """Validate only declared fields, while never inventing missing values."""
        payload = {"adapter_version": self.adapter_version, **payload}
        fields = StagingRecord.model_fields
        accepted: dict[str, Any] = {}
        for name, model_field in fields.items():
            alias = model_field.validation_alias
            if name in payload:
                accepted[name] = payload[name]
            elif isinstance(alias, str) and alias in payload:
                accepted[alias] = payload[alias]
        try:
            return StagingRecord.model_validate(accepted)
        except ValidationError as exc:
            source_id = payload.get("source_record_id", "<unknown>")
            raise ValueError(
                f"{self.source_name} record {source_id!r} violates StagingRecord: {exc}"
            ) from exc


def read_records(
    raw: Path | str | Mapping[str, Any] | Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(raw, Mapping):
        return [dict(raw)]
    if not isinstance(raw, (str, Path)):
        return [dict(item) for item in raw]

    path = Path(raw)
    suffix = path.suffix.lower()
    if suffix in {".jsonl", ".ndjson"}:
        with path.open("r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
    if suffix == ".json":
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        if isinstance(value, list):
            return [dict(item) for item in value]
        if isinstance(value, dict):
            for key in ("records", "features", "places", "results", "elements"):
                if isinstance(value.get(key), list):
                    return [dict(item) for item in value[key]]
            return [value]
        raise ValueError(f"{path}: expected a JSON object or array")
    if suffix in {".parquet", ".pq"}:
        try:
            import polars as pl
        except ImportError as exc:
            raise RuntimeError("polars is required to read Parquet source files") from exc
        return pl.read_parquet(path).to_dicts()
    raise ValueError(f"unsupported raw input format: {path.suffix or '<none>'}")


def region_value(region: Mapping[str, Any] | Any, name: str) -> Any:
    if isinstance(region, Mapping):
        if name in region:
            return region[name]
        bbox = region.get("bbox")
        if isinstance(bbox, Mapping) and name in bbox:
            return bbox[name]
    value = getattr(region, name, None)
    if value is not None:
        return value
    bbox = getattr(region, "bbox", None)
    if isinstance(bbox, Mapping):
        return bbox.get(name)
    return getattr(bbox, name, None) if bbox is not None else None


def first_value(data: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value_present(value):
            return value
    return None


def value_present(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _model_dump(record: StagingRecord) -> dict[str, Any]:
    return record.model_dump(mode="python")
