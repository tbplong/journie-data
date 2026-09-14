from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx

from journie_data.schemas.models import StagingRecord

from .base import FetchResult, FetchStatus, SourceAdapter, first_value, read_records, region_value

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
DEFAULT_USER_AGENT = "JournieDataBot/0.1 (research POI ingestion)"
POI_KEYS = ("tourism", "historic", "leisure", "amenity", "shop")
OSM_CATEGORY_MAP = {
    "tourism=museum": "attraction.museum",
    "tourism=gallery": "attraction.gallery",
    "tourism=viewpoint": "attraction.viewpoint",
    "tourism=attraction": "attraction.landmark",
    "historic=monument": "attraction.historic_site",
    "historic=memorial": "attraction.historic_site",
    "leisure=park": "nature.park",
    "leisure=garden": "nature.garden",
    "amenity=restaurant": "food.restaurant",
    "amenity=cafe": "food.cafe",
    "amenity=food_court": "food.food_court",
    "amenity=place_of_worship": "culture_religion.temple",
    "amenity=cinema": "entertainment.cinema",
    "amenity=theatre": "entertainment.performing_arts",
    "shop=mall": "shopping.mall",
    "shop=department_store": "shopping.department_store",
}


class OSMAdapter(SourceAdapter):
    source_name = "osm"
    adapter_version = "osm/v1"

    def __init__(
        self,
        *,
        overpass_url: str = OVERPASS_URL,
        local_path: Path | str | None = None,
        category_map: Mapping[str, str] | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.overpass_url = overpass_url
        self.local_path = Path(local_path) if local_path else None
        self.category_map = dict(category_map or OSM_CATEGORY_MAP)
        self.user_agent = user_agent

    def select_records(
        self, records: Iterable[Mapping[str, Any]]
    ) -> list[Mapping[str, Any]]:
        return [
            record
            for record in records
            if any(category in self.category_map for category in _source_categories(record))
        ]

    def fetch(self, region: Mapping[str, Any] | Any, snapshot_date: date | str) -> FetchResult:
        if self.local_path is not None:
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
                    self.source_name,
                    FetchStatus.FAILED,
                    raw_path=self.local_path,
                    message=str(exc),
                )
            return FetchResult(
                self.source_name,
                FetchStatus.SUCCESS if records else FetchStatus.EMPTY,
                records,
                self.local_path,
            )
        if self.client is None:
            return FetchResult(
                self.source_name,
                FetchStatus.NOT_CONFIGURED,
                message="an httpx.Client is required for Overpass fetches",
            )
        bounds = tuple(
            region_value(region, key) for key in ("min_lat", "min_lon", "max_lat", "max_lon")
        )
        if any(value is None for value in bounds):
            return FetchResult(
                self.source_name,
                FetchStatus.NOT_CONFIGURED,
                message="region bbox requires min_lat, min_lon, max_lat, and max_lon",
            )
        filters = "".join(
            f'nwr["{key}"="{value}"]["name"]'
            f"({bounds[0]},{bounds[1]},{bounds[2]},{bounds[3]});"
            for category in self.category_map
            for key, value in [category.split("=", 1)]
        )
        query = f"[out:json][timeout:90];({filters});out center tags;"
        try:
            response, attempts = self._request(
                "POST",
                self.overpass_url,
                data={"data": query},
                headers={"User-Agent": self.user_agent, "Accept": "application/json"},
            )
            payload = response.json()
            records = payload.get("elements", [])
            if not isinstance(records, list):
                raise ValueError("Overpass response has no elements array")
        except (httpx.HTTPError, ValueError) as exc:
            return FetchResult(self.source_name, FetchStatus.FAILED, message=str(exc))
        return FetchResult(
            self.source_name,
            FetchStatus.SUCCESS if records else FetchStatus.EMPTY,
            [dict(record) for record in records],
            http_attempts=attempts,
        )

    def include_record(self, raw: Mapping[str, Any]) -> bool:
        tags = raw.get("tags")
        return isinstance(tags, Mapping) and bool(str(tags.get("name", "")).strip())

    def normalize_record(self, raw: Mapping[str, Any], *, retrieved_at: datetime) -> StagingRecord:
        tags = raw.get("tags") if isinstance(raw.get("tags"), Mapping) else {}
        center = raw.get("center") if isinstance(raw.get("center"), Mapping) else {}
        osm_type = str(raw.get("type") or "unknown")
        osm_id = raw.get("id")
        if osm_id is None:
            raise ValueError("OSM record is missing an element ID")
        source_categories = _source_categories(raw)
        address_parts = [
            tags.get("addr:housenumber"),
            tags.get("addr:street"),
            tags.get("addr:suburb"),
            tags.get("addr:city"),
        ]
        address = ", ".join(str(part) for part in address_parts if part) or None
        payload = {
            "source": self.source_name,
            "source_record_id": f"{osm_type}/{osm_id}",
            "source_url": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
            "retrieved_at": retrieved_at,
            "name": tags.get("name"),
            "name_en": tags.get("name:en"),
            "latitude": first_value(raw, "lat")
            if first_value(raw, "lat") is not None
            else center.get("lat"),
            "longitude": first_value(raw, "lon")
            if first_value(raw, "lon") is not None
            else center.get("lon"),
            "address": address,
            "district": first_value(tags, "addr:district", "addr:suburb"),
            "city": tags.get("addr:city"),
            "website": first_value(tags, "website", "contact:website"),
            "phone": first_value(tags, "phone", "contact:phone"),
            "category": next(
                (
                    self.category_map[value]
                    for value in source_categories
                    if value in self.category_map
                ),
                None,
            ),
            "source_categories": source_categories,
            "opening_hours_raw": tags.get("opening_hours"),
            "price_raw": first_value(tags, "charge", "fee"),
            "operating_status": "closed" if tags.get("disused") == "yes" else None,
            "wikidata": tags.get("wikidata"),
            "wikipedia": tags.get("wikipedia"),
            "raw_tags": dict(tags),
            "raw_data": dict(raw),
        }
        return self._staging_record(payload)


def _source_categories(raw: Mapping[str, Any]) -> list[str]:
    tags = raw.get("tags") if isinstance(raw.get("tags"), Mapping) else {}
    return [f"{key}={tags[key]}" for key in POI_KEYS if tags.get(key)]
