from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

from journie_data.resolution.candidates import generate_candidate_pairs


def _value(row: object, field: str) -> Any:
    if hasattr(row, field):
        return getattr(row, field)
    return row.get(field) if isinstance(row, dict) else None


def benchmark_sources(records: Iterable[object]) -> list[dict[str, Any]]:
    grouped: dict[str, list[object]] = {}
    for record in records:
        grouped.setdefault(str(_value(record, "source")), []).append(record)
    output: list[dict[str, Any]] = []
    for source, rows in sorted(grouped.items()):
        count = len(rows)

        def coverage(field: str, source_rows: list[object] = rows, total: int = count) -> float:
            return round(
                sum(_value(row, field) not in (None, "", [], {}) for row in source_rows) / total,
                6,
            )

        categories = Counter(_value(row, "category") for row in rows if _value(row, "category"))
        candidates = generate_candidate_pairs(rows)
        output.append(
            {
                "source": source,
                "record_count": count,
                "named_pois": sum(bool(str(_value(row, "name") or "").strip()) for row in rows),
                "restaurant_count": categories["food.restaurant"],
                "cafe_count": categories["food.cafe"],
                "attraction_count": sum(
                    v for k, v in categories.items() if str(k).startswith("attraction.")
                ),
                "name_coverage": coverage("name"),
                "category_coverage": coverage("category"),
                "address_coverage": coverage("address"),
                "website_coverage": coverage("website"),
                "opening_hours_coverage": coverage("opening_hours_raw"),
                "price_coverage": coverage("price_raw"),
                "operating_status_coverage": coverage("operating_status"),
                "outside_region_count": sum(bool(_value(row, "outside_region")) for row in rows),
                "duplicate_candidate_count": len(candidates),
            }
        )
    return output


def category_distribution(records: Iterable[object]) -> list[dict[str, Any]]:
    counts = Counter(
        (str(_value(row, "source")), str(_value(row, "category") or "<unmapped>"))
        for row in records
    )
    return [
        {"source": source, "category": category, "count": count}
        for (source, category), count in sorted(counts.items())
    ]


def unmapped_categories(records: Iterable[object]) -> list[dict[str, Any]]:
    counts: Counter[tuple[str, str]] = Counter()
    for row in records:
        if _value(row, "category"):
            continue
        source_categories = _value(row, "source_categories") or ["<missing>"]
        for category in source_categories:
            counts[(str(_value(row, "source")), str(category))] += 1
    return [
        {"source": source, "source_category": category, "count": count}
        for (source, category), count in sorted(counts.items())
    ]


def missing_values(records: Iterable[object]) -> list[dict[str, Any]]:
    rows = list(records)
    fields = (
        "name",
        "category",
        "latitude",
        "longitude",
        "address",
        "website",
        "opening_hours_raw",
        "price_raw",
        "description",
    )
    grouped: dict[str, list[object]] = {}
    for row in rows:
        grouped.setdefault(str(_value(row, "source")), []).append(row)
    return [
        {
            "source": source,
            "field": field,
            "missing_count": sum(_value(row, field) in (None, "", [], {}) for row in source_rows),
            "record_count": len(source_rows),
        }
        for source, source_rows in sorted(grouped.items())
        for field in fields
    ]


def parser_failures(records: Iterable[object], field: str) -> list[dict[str, Any]]:
    normalized_field = f"{field}_normalized"
    raw_field = f"{field}_raw"
    if field == "price":
        normalized_field = "cost_type"
    failures = []
    for row in records:
        raw = _value(row, raw_field)
        normalized = _value(row, normalized_field)
        failed = bool(raw) and (normalized in (None, "", "unknown"))
        if failed:
            failures.append(
                {
                    "source": _value(row, "source"),
                    "source_record_id": _value(row, "source_record_id"),
                    "value_raw": raw,
                }
            )
    return failures
