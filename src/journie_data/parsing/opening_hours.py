"""Basic OSM and Vietnamese opening-hours parser.

Unsupported exceptions, public-holiday expressions, and free text deliberately fail.
"""

from __future__ import annotations

import re
import unicodedata

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DAY_ALIASES = {
    "mo": 0,
    "mon": 0,
    "monday": 0,
    "thu 2": 0,
    "thu hai": 0,
    "t2": 0,
    "tu": 1,
    "tue": 1,
    "tuesday": 1,
    "thu 3": 1,
    "thu ba": 1,
    "t3": 1,
    "we": 2,
    "wed": 2,
    "wednesday": 2,
    "thu 4": 2,
    "thu tu": 2,
    "t4": 2,
    "th": 3,
    "thu": 3,
    "thursday": 3,
    "thu 5": 3,
    "thu nam": 3,
    "t5": 3,
    "fr": 4,
    "fri": 4,
    "friday": 4,
    "thu 6": 4,
    "thu sau": 4,
    "t6": 4,
    "sa": 5,
    "sat": 5,
    "saturday": 5,
    "thu 7": 5,
    "thu bay": 5,
    "t7": 5,
    "su": 6,
    "sun": 6,
    "sunday": 6,
    "chu nhat": 6,
    "cn": 6,
}
_TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-4])(?:\s*[:h]\s*([0-5]\d))?(?!\d)", re.I)
_DAY_TOKEN = re.compile(
    r"thứ\s*(?:2|3|4|5|6|7|hai|ba|tư|năm|sáu|bảy)|chủ\s*nhật|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"mon|tue|wed|thu|fri|sat|sun|mo|tu|we|th|fr|sa|su|t[2-7]|cn",
    re.I,
)


def _plain(value: str) -> str:
    value = value.casefold().replace("đ", "d")
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))


def _day_index(token: str) -> int | None:
    return _DAY_ALIASES.get(re.sub(r"\s+", " ", _plain(token)).strip())


def _days(expression: str) -> list[int] | None:
    tokens = _DAY_TOKEN.findall(expression)
    if not tokens:
        plain = _plain(expression)
        if any(term in plain for term in ("hang ngay", "moi ngay", "ca tuan", "all week", "daily")):
            return list(range(7))
        return None
    indices = [_day_index(token) for token in tokens]
    if any(index is None for index in indices):
        return None
    valid_indices = [index for index in indices if index is not None]
    if len(valid_indices) == 2 and re.search(r"[-–]", expression):
        start, end = valid_indices
        return [(start + offset) % 7 for offset in range((end - start) % 7 + 1)]
    return list(dict.fromkeys(valid_indices))


def _first_time(expression: str) -> re.Match[str] | None:
    day_spans = [match.span() for match in _DAY_TOKEN.finditer(expression)]
    return next(
        (
            match
            for match in _TIME_RE.finditer(expression)
            if not any(start <= match.start() < end for start, end in day_spans)
        ),
        None,
    )


def _time_intervals(expression: str) -> list[list[str]] | None:
    matches = list(_TIME_RE.finditer(expression))
    if not matches or len(matches) % 2:
        return None
    intervals: list[list[str]] = []
    for start_match, end_match in zip(matches[::2], matches[1::2], strict=True):
        between = expression[start_match.end() : end_match.start()]
        if not re.fullmatch(r"\s*[-–]\s*", between):
            return None
        start_hour, start_minute = int(start_match.group(1)), int(start_match.group(2) or 0)
        end_hour, end_minute = int(end_match.group(1)), int(end_match.group(2) or 0)
        if start_hour == 24 and start_minute != 0 or end_hour == 24 and end_minute != 0:
            return None
        intervals.append(
            [f"{start_hour:02d}:{start_minute:02d}", f"{end_hour:02d}:{end_minute:02d}"]
        )
    return intervals


def parse_opening_hours(value: str | None) -> dict[str, list[list[str]]] | None:
    """Parse basic weekly hours; return ``None`` for unsupported/ambiguous text."""
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    plain = _plain(text)
    if plain in {"24/7", "24h/7", "always open", "mo cua 24/7"}:
        return {day: [["00:00", "24:00"]] for day in DAYS}
    if re.search(r"\b(?:off|closed|ph|sh|unknown)\b", plain):
        return None

    schedule: dict[str, list[list[str]]] = {day: [] for day in DAYS}
    segments = [part.strip() for part in re.split(r"\s*;\s*", text) if part.strip()]
    for segment in segments:
        first_time = _first_time(segment)
        if not first_time:
            return None
        day_part, time_part = (
            segment[: first_time.start()].strip(" :,"),
            segment[first_time.start() :],
        )
        selected_days = _days(day_part) if day_part else list(range(7))
        intervals = _time_intervals(time_part)
        if selected_days is None or intervals is None:
            return None
        for day_index in selected_days:
            schedule[DAYS[day_index]].extend(intervals)
    return schedule
