"""netkeiba スクレイパーの低レベルパース関数のテスト。"""

import pytest

from src.collector.scraper import _core


def test_parse_race_key_valid():
    info = _core.parse_race_key("202405021211")
    assert info["year"] == "2024"
    assert info["venue"] == "東京"  # 場コード 05
    assert info["race_number"] == 11


@pytest.mark.parametrize("bad", ["20240502121", "abcd05021211", ""])
def test_parse_race_key_invalid(bad):
    with pytest.raises(ValueError):
        _core.parse_race_key(bad)


@pytest.mark.parametrize(
    "text,expected",
    [("牡3", ("牡", 3)), ("牝4", ("牝", 4)), ("セ5", ("セ", 5)), ("せ6", ("セ", 6))],
)
def test_parse_sex_age(text, expected):
    assert _core._parse_sex_age(text) == expected


def test_parse_sex_age_unparseable():
    assert _core._parse_sex_age("---") == (None, None)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("456(-12)", (456, -12)),
        ("428(+4)", (428, 4)),
        ("500(0)", (500, 0)),
        ("456", (456, None)),  # 初出走(増減なし)
        ("計不", (None, None)),  # 未計量
    ],
)
def test_parse_horse_weight(text, expected):
    assert _core._parse_horse_weight(text) == expected
