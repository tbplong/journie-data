"""Small geo helpers that do not require a GIS dependency."""

from __future__ import annotations

from collections.abc import Mapping
from math import asin, cos, radians, sin, sqrt
from typing import Any

EARTH_RADIUS_M = 6_371_008.8


def valid_coordinates(latitude: object, longitude: object) -> bool:
    """Return whether values are finite coordinates in WGS84 bounds."""
    if isinstance(latitude, bool) or isinstance(longitude, bool):
        return False
    try:
        lat, lon = float(latitude), float(longitude)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return lat == lat and lon == lon and -90 <= lat <= 90 and -180 <= lon <= 180


def haversine_distance_m(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    """Calculate great-circle distance in metres between WGS84 coordinates."""
    if not valid_coordinates(latitude_a, longitude_a) or not valid_coordinates(
        latitude_b, longitude_b
    ):
        raise ValueError("coordinates must be finite and within WGS84 bounds")
    lat_a, lat_b = radians(float(latitude_a)), radians(float(latitude_b))
    delta_lat = lat_b - lat_a
    delta_lon = radians(float(longitude_b) - float(longitude_a))
    haversine = sin(delta_lat / 2) ** 2 + cos(lat_a) * cos(lat_b) * sin(delta_lon / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(min(1.0, haversine)))


def haversine_km(
    latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float
) -> float:
    return haversine_distance_m(latitude_a, longitude_a, latitude_b, longitude_b) / 1000


def point_in_geojson(longitude: float, latitude: float, geojson: Mapping[str, Any]) -> bool:
    """Return whether a point is inside any Polygon ring using WGS84 coordinates."""
    geometry_type = geojson.get("type")
    if geometry_type == "FeatureCollection":
        return any(
            point_in_geojson(longitude, latitude, feature)
            for feature in geojson.get("features", [])
        )
    if geometry_type == "Feature":
        geometry = geojson.get("geometry")
        return isinstance(geometry, Mapping) and point_in_geojson(longitude, latitude, geometry)
    coordinates = geojson.get("coordinates", [])
    polygons = [coordinates] if geometry_type == "Polygon" else coordinates
    if geometry_type not in {"Polygon", "MultiPolygon"}:
        return False
    for polygon in polygons:
        if not polygon or not _point_in_ring(longitude, latitude, polygon[0]):
            continue
        if not any(_point_in_ring(longitude, latitude, hole) for hole in polygon[1:]):
            return True
    return False


def geojson_has_polygon(geojson: Mapping[str, Any]) -> bool:
    geometry_type = geojson.get("type")
    if geometry_type == "FeatureCollection":
        return any(
            isinstance(feature, Mapping) and geojson_has_polygon(feature)
            for feature in geojson.get("features", [])
        )
    if geometry_type == "Feature":
        geometry = geojson.get("geometry")
        return isinstance(geometry, Mapping) and geojson_has_polygon(geometry)
    coordinates = geojson.get("coordinates")
    return geometry_type in {"Polygon", "MultiPolygon"} and bool(coordinates)


def _point_in_ring(longitude: float, latitude: float, ring: list[list[float]]) -> bool:
    inside = False
    previous = ring[-1]
    for current in ring:
        x1, y1 = previous
        x2, y2 = current
        crosses = (y1 > latitude) != (y2 > latitude)
        if crosses and longitude < (x2 - x1) * (latitude - y1) / (y2 - y1) + x1:
            inside = not inside
        previous = current
    return inside
