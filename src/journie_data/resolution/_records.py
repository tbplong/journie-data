"""Duck-typed record access shared by resolution modules."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_MULTI_TENANT_WEBSITE_HOSTS = {
    "facebook.com",
    "fb.com",
    "google.com",
    "instagram.com",
    "linktr.ee",
    "m.facebook.com",
    "sites.google.com",
    "tiktok.com",
    "zalo.me",
}


def get(record: object, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(record, Mapping) and name in record:
            return record[name]
        if hasattr(record, name):
            return getattr(record, name)
    return default


def identity(record: object) -> tuple[str, str]:
    source = get(record, "source", "source_name", default="")
    source = getattr(source, "value", source)
    record_id = get(record, "source_record_id", "record_id", "id", default="")
    return str(source), str(record_id)


def strong_identifiers(record: object) -> set[tuple[str, str]]:
    from journie_data.normalization.contact import normalize_phone, normalize_website

    result: set[tuple[str, str]] = set()
    aliases = {
        "wikidata": ("wikidata", "wikidata_id"),
        "website": ("normalized_website", "website_domain"),
        "phone": ("normalized_phone",),
    }
    raw_tags = get(record, "raw_tags", default={}) or {}
    for kind, names in aliases.items():
        value = get(record, *names)
        if value is None and isinstance(raw_tags, Mapping):
            value = next((raw_tags[name] for name in names if raw_tags.get(name)), None)
        if value is None and kind == "website":
            value = normalize_website(get(record, "website"))
        if value is None and kind == "phone":
            value = normalize_phone(get(record, "phone"))
        if kind == "website" and value == normalize_website(get(record, "source_url")):
            value = None
        if kind == "website" and value in _MULTI_TENANT_WEBSITE_HOSTS:
            value = None
        if value:
            result.add((kind, str(value).strip().casefold()))
    return result
