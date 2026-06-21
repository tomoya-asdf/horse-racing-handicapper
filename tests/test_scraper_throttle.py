"""送出間隔スロットルと券種オッズ並列取得・単一レース取得のテスト。"""

import requests

from src.collector.scraper import _core, odds, races


class _Clock:
    """time.monotonic の差し替え用。手動で時刻を進められる単調増加クロック。"""

    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _install_clock(monkeypatch, interval=1.0):
    clock = _Clock()
    sleeps: list[float] = []

    def fake_sleep(seconds):
        # スロットルの待ちはそのまま時刻を進めたものとして扱う(実時間は待たない)
        sleeps.append(seconds)
        clock.advance(seconds)

    monkeypatch.setattr(_core.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(_core.time, "sleep", fake_sleep)
    monkeypatch.setattr(_core.settings, "SCRAPER_REQUEST_INTERVAL_SECONDS", interval)
    monkeypatch.setattr(_core, "_next_request_at", 0.0)
    return clock, sleeps


def test_throttle_spaces_requests_by_interval(monkeypatch):
    clock, sleeps = _install_clock(monkeypatch, interval=1.0)

    _core._throttle()  # 初回は待たない
    assert sleeps == []

    clock.advance(0.3)  # 0.3秒経過 → 残り0.7秒だけ待つ(通信時間を間隔に算入)
    _core._throttle()
    assert sleeps == [0.7]


def test_throttle_no_wait_when_interval_already_elapsed(monkeypatch):
    clock, sleeps = _install_clock(monkeypatch, interval=1.0)

    _core._throttle()
    clock.advance(2.5)  # 間隔より長く経過していれば待たない
    _core._throttle()
    assert sleeps == []


def test_throttle_disabled_when_interval_zero(monkeypatch):
    _, sleeps = _install_clock(monkeypatch, interval=0.0)
    _core._throttle()
    _core._throttle()
    assert sleeps == []


def test_fetch_supported_odds_returns_all_bet_types(monkeypatch):
    calls: list[str] = []

    def fake_fetch(race_id, bet_type):
        calls.append(bet_type)
        return {bet_type: 1.0}

    monkeypatch.setattr(odds, "fetch_bet_type_odds", fake_fetch)
    result = odds.fetch_supported_odds("202405021211")

    assert set(result) == {
        odds.BET_TYPE_WIN,
        odds.BET_TYPE_PLACE,
        odds.BET_TYPE_QUINELLA,
        odds.BET_TYPE_WIDE,
    }
    assert result[odds.BET_TYPE_WIN] == {odds.BET_TYPE_WIN: 1.0}
    assert set(calls) == set(result)  # 各券種が1回ずつ取得される


def test_fetch_single_race_delegates_and_defaults_to_include_started(monkeypatch):
    captured = {}

    def fake_build(race_id, target_date, now, include_started, rendered_state):
        captured["race_id"] = race_id
        captured["include_started"] = include_started
        return {"race_key": race_id}

    monkeypatch.setattr(races, "_build_race_detail", fake_build)
    result = races.fetch_single_race("202405021211", races.date(2024, 5, 21))

    assert result == {"race_key": "202405021211"}
    assert captured["race_id"] == "202405021211"
    assert captured["include_started"] is True  # 直前で発走を跨いでも取得する


def test_fetch_single_race_returns_none_on_request_error(monkeypatch):
    def boom(*_a, **_k):
        raise requests.RequestException("down")

    monkeypatch.setattr(races, "_build_race_detail", boom)
    assert races.fetch_single_race("202405021211", races.date(2024, 5, 21)) is None
