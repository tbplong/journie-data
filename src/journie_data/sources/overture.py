from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

from journie_data.schemas.models import StagingRecord

from .base import (
    FetchResult,
    FetchStatus,
    SourceAdapter,
    first_value,
    read_records,
    region_value,
)

OVERTURE_CATEGORY_MAP = {
    "cafe": "food.cafe",
    "coffee_shop": "food.cafe",
    "restaurant": "food.restaurant",
    "museum": "attraction.museum",
    "art_gallery": "attraction.gallery",
    "historical_place": "attraction.historic_site",
    "landmark_and_historical_building": "attraction.landmark",
    "park": "nature.park",
    "garden": "nature.garden",
    "shopping_mall": "shopping.mall",
    "market": "shopping.market",
    "church": "culture_religion.church",
    "temple": "culture_religion.temple",
    "pagoda": "culture_religion.pagoda",
}


class OvertureAdapter(SourceAdapter):
    source_name = "overture"
    adapter_version = "overture/v1"

    def __init__(
        self,
        *,
        local_path: Path | str | None = None,
        category_map: Mapping[str, str] | None = None,
        remote_uri_template: str = (
            "s3://overturemaps-us-west-2/release/{release}/theme=places/type=place/*"
        ),
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.local_path = Path(local_path) if local_path else None
        self.category_map = dict(category_map or OVERTURE_CATEGORY_MAP)
        self.remote_uri_template = remote_uri_template

    def select_records(
        self, records: Iterable[Mapping[str, Any]]
    ) -> list[Mapping[str, Any]]:
        return [
            record
            for record in records
            if any(
                category.casefold() in self.category_map
                for category in _source_categories(record)
            )
        ]

    def fetch(self, region: Mapping[str, Any] | Any, snapshot_date: date | str) -> FetchResult:
        if self.local_path is None:
            release = str(snapshot_date)
            if release == "latest" or not re.fullmatch(r"[A-Za-z0-9._-]+", release):
                return FetchResult(
                    self.source_name,
                    FetchStatus.NOT_CONFIGURED,
                    message="a pinned Overture release ID is required instead of 'latest'",
                )
            bounds = {
                key: region_value(region, key)
                for key in ("min_lon", "min_lat", "max_lon", "max_lat")
            }
            if any(value is None for value in bounds.values()):
                return FetchResult(
                    self.source_name,
                    FetchStatus.NOT_CONFIGURED,
                    message="region bbox is required for remote Overture acquisition",
                )
            try:
                import duckdb

                connection = duckdb.connect()
                connection.execute("INSTALL httpfs; LOAD httpfs;")
                connection.execute("SET s3_region='us-west-2'; SET s3_url_style='path';")
                uri = self.remote_uri_template.format(release=release).replace("'", "''")
                query = f"""
                    SELECT * EXCLUDE (geometry),
                           (bbox.xmin + bbox.xmax) / 2 AS longitude,
                           (bbox.ymin + bbox.ymax) / 2 AS latitude
                    FROM read_parquet('{uri}', hive_partitioning=1)
                    WHERE bbox.xmin <= ? AND bbox.xmax >= ?
                      AND bbox.ymin <= ? AND bbox.ymax >= ?
                      AND names.primary IS NOT NULL
                """
                records = (
                    connection.execute(
                        query,
                        [
                            bounds["max_lon"],
                            bounds["min_lon"],
                            bounds["max_lat"],
                            bounds["min_lat"],
                        ],
                    )
                    .pl()
                    .to_dicts()
                )
                connection.close()
            except Exception as exc:
                return FetchResult(
                    self.source_name,
                    FetchStatus.FAILED,
                    message=f"Overture release {release} query failed: {exc}",
                )
            return FetchResult(
                self.source_name,
                FetchStatus.SUCCESS if records else FetchStatus.EMPTY,
                records,
                message=f"Overture release {release}",
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
        status = FetchStatus.SUCCESS if records else FetchStatus.EMPTY
        return FetchResult(self.source_name, status, records, self.local_path)

    def normalize_record(self, raw: Mapping[str, Any], *, retrieved_at: datetime) -> StagingRecord:
        properties = raw.get("properties") if isinstance(raw.get("properties"), Mapping) else raw
        geometry = raw.get("geometry") if isinstance(raw.get("geometry"), Mapping) else {}
        coordinates = geometry.get("coordinates", [])
        names = properties.get("names") if isinstance(properties.get("names"), Mapping) else {}
        primary_name = names.get("primary")
        if isinstance(primary_name, Mapping):
            primary_name = first_value(primary_name, "value", "name")
        source_categories = _source_categories(properties)
        category = next(
            (
                self.category_map[value.casefold()]
                for value in source_categories
                if value.casefold() in self.category_map
            ),
            None,
        )

        addresses = properties.get("addresses")
        address_data = addresses[0] if isinstance(addresses, list) and addresses else addresses
        address_data = address_data if isinstance(address_data, Mapping) else {}
        websites = properties.get("websites")
        website = websites[0] if isinstance(websites, list) and websites else websites
        phones = properties.get("phones")
        phone = phones[0] if isinstance(phones, list) and phones else phones
        source_id = first_value(properties, "id", "place_id", "source_id") or raw.get("id")
        if source_id is None:
            raise ValueError("overture record is missing a stable source ID")

        payload = {
            "source": self.source_name,
            "source_record_id": str(source_id),
            "source_url": first_value(properties, "source_url", "url"),
            "retrieved_at": retrieved_at,
            "name": first_value(properties, "name") or primary_name,
            "name_en": names.get("common", {}).get("en")
            if isinstance(names.get("common"), Mapping)
            else None,
            "latitude": coordinates[1]
            if len(coordinates) >= 2
            else first_value(properties, "latitude", "lat"),
            "longitude": coordinates[0]
            if len(coordinates) >= 2
            else first_value(properties, "longitude", "lon", "lng"),
            "address": first_value(address_data, "freeform", "address", "street_address"),
            "district": first_value(address_data, "county", "district"),
            "city": first_value(address_data, "locality", "city"),
            "website": website,
            "phone": phone,
            "category": category,
            "source_categories": source_categories,
            "operating_status": first_value(properties, "operating_status", "status"),
            "raw_data": dict(raw),
        }
        return self._staging_record(payload)


def _source_categories(raw: Mapping[str, Any]) -> list[str]:
    properties = raw.get("properties") if isinstance(raw.get("properties"), Mapping) else raw
    categories = properties.get("categories")
    if isinstance(categories, Mapping):
        alternate = categories.get("alternate") or []
        alternate = alternate if isinstance(alternate, list) else [alternate]
        values = [categories.get("primary"), *alternate]
    else:
        values = categories if isinstance(categories, list) else [categories]
    return [str(value) for value in values if value]
