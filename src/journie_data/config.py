from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class BBox(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_lon: float = Field(ge=-180, le=180)
    min_lat: float = Field(ge=-90, le=90)
    max_lon: float = Field(ge=-180, le=180)
    max_lat: float = Field(ge=-90, le=90)

    @model_validator(mode="after")
    def ordered(self) -> BBox:
        if self.min_lon >= self.max_lon or self.min_lat >= self.max_lat:
            raise ValueError("bbox minimums must be lower than maximums")
        return self


class Region(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    country_code: str
    timezone: str
    bbox: BBox
    polygon_file: Path
    boundary_source: str
    boundary_note: str | None = None


def load_yaml(path: Path | str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle) or {}
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a YAML object")
    return value


def load_region(region_id: str, config_root: Path | str = "configs") -> Region:
    root = Path(config_root)
    path = root / "regions" / f"{region_id}.yaml"
    value = load_yaml(path)
    region = Region.model_validate(value.get("region"))
    polygon = region.polygon_file
    if not polygon.is_absolute():
        polygon = Path.cwd() / polygon
    if not polygon.exists():
        raise ValueError(f"region polygon does not exist: {polygon}")
    with polygon.open("r", encoding="utf-8") as handle:
        geojson = json.load(handle)
    if geojson.get("type") not in {"Feature", "FeatureCollection", "Polygon", "MultiPolygon"}:
        raise ValueError(f"invalid GeoJSON type in {polygon}")
    return region


def load_pipeline_config(config_root: Path | str = "configs") -> dict[str, Any]:
    root = Path(config_root)
    names = (
        "sources.yaml",
        "field_priorities.v1.yaml",
        "duration.v1.yaml",
        "cost.v1.yaml",
        "eligibility.v1.yaml",
    )
    return {name: load_yaml(root / name) for name in names}
