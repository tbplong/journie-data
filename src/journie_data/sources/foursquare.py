from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

from journie_data.schemas.models import StagingRecord

from .base import FetchResult, FetchStatus, SourceAdapter, first_value, read_records

FSQ_CATEGORY_KEYWORDS = (
    ("coffee", "food.cafe"),
    ("cafe", "food.cafe"),
    ("restaurant", "food.restaurant"),
    ("museum", "attraction.museum"),
    ("gallery", "attraction.gallery"),
    ("historic", "attraction.historic_site"),
    ("park", "nature.park"),
    ("garden", "nature.garden"),
    ("market", "shopping.market"),
    ("mall", "shopping.mall"),
    ("cinema", "entertainment.cinema"),
    ("theater", "entertainment.performing_arts"),
    ("theatre", "entertainment.performing_arts"),
)


class FoursquareAdapter(SourceAdapter):
    source_name = "foursquare"
    adapter_version = "foursquare/v1"

    def __init__(
        self,
        *,
        local_path: Path | str | None = None,
        category_map: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.local_path = Path(local_path) if local_path else None
        self.category_keywords = tuple((category_map or dict(FSQ_CATEGORY_KEYWORDS)).items())

    def fetch(self, region: Mapping[str, Any] | Any, snapshot_date: date | str) -> FetchResult:
        if self.local_path is None:
            return FetchResult(
                self.source_name,
                FetchStatus.DEFERRED,
                message=(
                    "remote Foursquare access is not configured; provide a local JSON, "
                    "JSONL, or Parquet export"
                ),
            )
        if not self.local_path.exists():
            return FetchResult(
                self.source_name,
                FetchStatus.NOT_CONFIGURED,
                raw_path=self.local_path,
                message="configured local input does not exist",
            )
        try:
            records = read_records(self.local_path)
        except (OSError, ValueError, RuntimeError) as exc:
            return FetchResult(
                self.source_name, FetchStatus.FAILED, raw_path=self.local_path, message=str(exc)
            )
        return FetchResult(
            self.source_name,
            FetchStatus.SUCCESS if records else FetchStatus.EMPTY,
            records,
            self.local_path,
        )

    def normalize_record(self, raw: Mapping[str, Any], *, retrieved_at: datetime) -> StagingRecord:
        location = raw.get("location") if isinstance(raw.get("location"), Mapping) else {}
        geocodes = raw.get("geocodes") if isinstance(raw.get("geocodes"), Mapping) else {}
        point = geocodes.get("main") if isinstance(geocodes.get("main"), Mapping) else geocodes
        categories = raw.get("categories") if isinstance(raw.get("categories"), list) else []
        category_labels = [
            first_value(category, "name", "label", "id")
            for category in categories
            if isinstance(category, Mapping)
        ]
        category_labels = [str(value) for value in category_labels if value]
        category = next(
            (
                mapped
                for label in category_labels
                for keyword, mapped in self.category_keywords
                if keyword in label.lower()
            ),
            None,
        )
        date_closed = first_value(raw, "date_closed", "closed_at")
        source_id = first_value(raw, "fsq_place_id", "fsq_id", "id")
        if source_id is None:
            raise ValueError("Foursquare record is missing a stable source ID")
        latitude = first_value(raw, "latitude", "lat")
        longitude = first_value(raw, "longitude", "lon", "lng")
        payload = {
            "source": self.source_name,
            "source_record_id": str(source_id),
            "source_url": first_value(raw, "link", "source_url"),
            "retrieved_at": retrieved_at,
            "name": raw.get("name"),
            "latitude": latitude if latitude is not None else first_value(point, "latitude", "lat"),
            "longitude": longitude
            if longitude is not None
            else first_value(point, "longitude", "lon", "lng"),
            "address": first_value(raw, "address", "formatted_address")
            or first_value(location, "formatted_address", "address"),
            "district": first_value(location, "neighborhood", "district"),
            "city": first_value(raw, "locality") or first_value(location, "locality", "city"),
            "website": first_value(raw, "website", "homepage"),
            "phone": first_value(raw, "telephone", "tel", "phone"),
            "category": category,
            "source_categories": category_labels,
            "operating_status": "closed"
            if date_closed
            else first_value(raw, "status", "operating_status"),
            "source_updated_at": first_value(raw, "date_refreshed", "updated_at"),
            "raw_data": dict(raw),
        }
        return self._staging_record(payload)
