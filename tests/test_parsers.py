from __future__ import annotations

import pytest

from journie_data.parsing.opening_hours import DAYS, parse_opening_hours
from journie_data.parsing.prices import Price, parse_price


@pytest.mark.parametrize(
    "raw",
    [
        "Mo-Su 08:00-17:00",
        "Thứ 2 - Chủ nhật: 8:00 - 17:00",
        "hàng ngày: 8h00 - 17h00",
    ],
)
def test_weeklong_opening_hours(raw: str) -> None:
    assert parse_opening_hours(raw) == {day: [["08:00", "17:00"]] for day in DAYS}


def test_opening_hours_support_split_intervals_and_overnight_week_wrap() -> None:
    schedule = parse_opening_hours("Mo-Fr 08:00-12:00, 13:00-17:00; Sa-Su 09:00-11:00")

    assert schedule is not None
    assert schedule["mon"] == [["08:00", "12:00"], ["13:00", "17:00"]]
    assert schedule["sat"] == [["09:00", "11:00"]]
    assert schedule["sun"] == [["09:00", "11:00"]]


def test_opening_hours_never_invent_unknown_or_exception_hours() -> None:
    assert parse_opening_hours(None) is None
    assert parse_opening_hours("call ahead") is None
    assert parse_opening_hours("Mo-Fr 08:00-17:00; PH off") is None
    assert parse_opening_hours("24/7") == {day: [["00:00", "24:00"]] for day in DAYS}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Miễn phí", Price(0, 0, 0, "free")),
        ("40.000 đồng/người", Price(40_000, 40_000, 40_000, "ticket")),
        ("Adult: 80k", Price(80_000, 80_000, 80_000, "ticket")),
        ("50.000 - 100.000 VNĐ", Price(50_000, None, 100_000, "ticket")),
        ("about 10 USD", Price(None, None, None, "unknown")),
        ("giá thay đổi", Price(None, None, None, "unknown")),
    ],
)
def test_price_parsing_is_conservative(raw: str, expected: Price) -> None:
    assert parse_price(raw) == expected


def test_price_type_is_explicit_and_validated() -> None:
    assert parse_price("70k", "expected_spend").type == "expected_spend"
    with pytest.raises(ValueError, match="price_type"):
        parse_price("70k", "estimate")
