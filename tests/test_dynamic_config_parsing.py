"""動的設定の検証・正規化(parse_setting)のテスト。DBアクセスはしない純粋関数。"""

import pytest

from src.common.dynamic_config.parsing import parse_exact_time, parse_setting, weekdays_from_str


def test_betting_mode():
    assert parse_setting("betting_mode", "prod") == "prod"
    with pytest.raises(ValueError):
        parse_setting("betting_mode", "bad")


@pytest.mark.parametrize("bad", [150, 50, -100, 0])
def test_bet_amount_rejects_non_100_unit(bad):
    with pytest.raises(ValueError):
        parse_setting("bet_amount", bad)


def test_bet_amount_ok():
    assert parse_setting("bet_amount", 200) == 200


def test_bet_score_threshold_range():
    assert parse_setting("bet_score_threshold", 0.2) == 0.2
    with pytest.raises(ValueError):
        parse_setting("bet_score_threshold", 1.2)


def test_schedule_enabled_bool():
    assert parse_setting("schedule_collect_enabled", "true") is True
    assert parse_setting("schedule_collect_enabled", "off") is False
    with pytest.raises(ValueError):
        parse_setting("schedule_collect_enabled", "maybe")


def test_schedule_days_normalization():
    # 重複・順不同を昇順かつ一意に正規化する。配列でもカンマ区切りでも受ける。
    assert parse_setting("schedule_collect_days", [3, 1, 1, 5]) == "1,3,5"
    assert parse_setting("schedule_collect_days", "6,0,0") == "0,6"
    with pytest.raises(ValueError):
        parse_setting("schedule_collect_days", "7")


def test_schedule_interval_minimum():
    assert parse_setting("schedule_collect_interval_minutes", "30") == 30
    with pytest.raises(ValueError):
        parse_setting("schedule_collect_interval_minutes", "0")
    # 空はNone(未設定)
    assert parse_setting("schedule_collect_interval_minutes", "") is None


def test_exact_time():
    assert parse_exact_time("k", "9:5") == "09:05"
    assert parse_exact_time("k", "") is None
    with pytest.raises(ValueError):
        parse_exact_time("k", "25:00")


def test_weekdays_from_str_roundtrip():
    assert weekdays_from_str("1,3,5") == frozenset({1, 3, 5})
    assert weekdays_from_str("") == frozenset()


def test_model_param_validation():
    assert parse_setting("model_num_leaves", "31") == 31
    with pytest.raises(ValueError):
        parse_setting("model_num_leaves", "1")  # 2以上
    assert parse_setting("model_max_depth", "-1") == -1
    with pytest.raises(ValueError):
        parse_setting("model_learning_rate", "0")  # 0より大


def test_unknown_key_rejected():
    with pytest.raises(ValueError):
        parse_setting("totally_unknown_key", "x")
