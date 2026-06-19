"""起動時設定(pydantic-settings)の型・範囲検証のテスト。"""

import pytest
from pydantic import ValidationError

from src.common.config import Settings


def _settings(**overrides):
    # .env を読まず、既定値 + 明示指定だけで検証する
    return Settings(_env_file=None, **overrides)


def test_defaults_are_valid():
    s = _settings()
    assert s.BETTING_MODE == "sim"
    assert s.BET_AMOUNT == 100
    assert s.DB_POOL_SIZE >= 1


def test_invalid_betting_mode_rejected():
    with pytest.raises(ValidationError):
        _settings(BETTING_MODE="production")


@pytest.mark.parametrize("amount", [50, 150, 0, -100])
def test_bet_amount_must_be_100_unit(amount):
    with pytest.raises(ValidationError):
        _settings(BET_AMOUNT=amount)


def test_bet_amount_valid_multiple():
    assert _settings(BET_AMOUNT=300).BET_AMOUNT == 300


def test_negative_interval_rejected():
    with pytest.raises(ValidationError):
        _settings(COLLECT_INTERVAL_MINUTES=0)


def test_score_threshold_range():
    with pytest.raises(ValidationError):
        _settings(BET_SCORE_THRESHOLD=1.5)
    assert _settings(BET_SCORE_THRESHOLD=0.3).BET_SCORE_THRESHOLD == 0.3
