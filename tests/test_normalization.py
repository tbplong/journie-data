from __future__ import annotations

import math

import pytest

from journie_data.normalization.contact import normalize_phone, normalize_website
from journie_data.normalization.geo import haversine_distance_m, valid_coordinates
from journie_data.normalization.names import normalize_name, normalize_name_ascii


def test_vietnamese_name_keys_preserve_display_accents_but_offer_ascii_matching() -> None:
    value = "  BẢO TÀNG—Thành phố  Hồ Chí Minh! "

    assert normalize_name(value) == "bảo tàng thành phố hồ chí minh"
    assert normalize_name_ascii(value) == "bao tang thanh pho ho chi minh"
    assert normalize_name("  ") is None
    assert normalize_name_ascii(None) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://WWW.Example.COM/path?q=1", "example.com"),
        ("example.com.", "example.com"),
        ("https://münchen.example/place", "xn--mnchen-3ya.example"),
        ("localhost/path", None),
        (" ", None),
    ],
)
def test_website_normalization(value: str, expected: str | None) -> None:
    assert normalize_website(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("090 123 4567", "+84901234567"),
        ("(+84) 90-123-4567 máy lẻ 12", "+84901234567"),
        ("0084 28 3829 7334", "+842838297334"),
        ("123", None),
    ],
)
def test_vietnamese_phone_normalization(value: str, expected: str | None) -> None:
    assert normalize_phone(value) == expected


def test_coordinate_validation_and_distance() -> None:
    assert valid_coordinates("10.77916", "106.69979")
    assert not valid_coordinates(True, 106.7)
    assert not valid_coordinates(math.inf, 106.7)
    assert not valid_coordinates(91, 106.7)
    assert haversine_distance_m(10.77916, 106.69979, 10.77916, 106.69979) == 0
    assert haversine_distance_m(10.77916, 106.69979, 10.78016, 106.69979) == pytest.approx(
        111.2, abs=0.5
    )

    with pytest.raises(ValueError, match="WGS84"):
        haversine_distance_m(91, 0, 0, 0)
