"""スクレイパーHTTPクライアントのリトライ/バックオフのテスト。"""

import requests

from src.collector.scraper import _core


class _Resp:
    def __init__(self, status_code=200, content_type="text/html; charset=UTF-8"):
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"status {self.status_code}")


def _patch(monkeypatch, settings_overrides=None):
    # バックオフのsleepは即時に(テストを高速化)
    monkeypatch.setattr(_core.time, "sleep", lambda *_a, **_k: None)
    if settings_overrides:
        for k, v in settings_overrides.items():
            monkeypatch.setattr(_core.settings, k, v)


def test_retries_then_succeeds(monkeypatch):
    _patch(monkeypatch, {"SCRAPER_MAX_RETRIES": 3, "SCRAPER_RETRY_BACKOFF_SECONDS": 0.01})
    calls = {"n": 0}

    def fake_get(url, timeout=10, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.exceptions.ConnectionError("boom")
        return _Resp(200)

    client = _core.NetkeibaHttpClient()
    monkeypatch.setattr(client.session, "get", fake_get)
    resp = client.get("https://example.com")
    assert resp.status_code == 200
    assert calls["n"] == 3  # 2回失敗 + 3回目成功


def test_retryable_status_then_success(monkeypatch):
    _patch(monkeypatch, {"SCRAPER_MAX_RETRIES": 2, "SCRAPER_RETRY_BACKOFF_SECONDS": 0.01})
    calls = {"n": 0}

    def fake_get(url, timeout=10, **kwargs):
        calls["n"] += 1
        return _Resp(503) if calls["n"] == 1 else _Resp(200)

    client = _core.NetkeibaHttpClient()
    monkeypatch.setattr(client.session, "get", fake_get)
    resp = client.get("https://example.com")
    assert resp.status_code == 200
    assert calls["n"] == 2


def test_gives_up_after_max_retries(monkeypatch):
    _patch(monkeypatch, {"SCRAPER_MAX_RETRIES": 2, "SCRAPER_RETRY_BACKOFF_SECONDS": 0.01})
    calls = {"n": 0}

    def fake_get(url, timeout=10, **kwargs):
        calls["n"] += 1
        raise requests.exceptions.ConnectionError("always down")

    client = _core.NetkeibaHttpClient()
    monkeypatch.setattr(client.session, "get", fake_get)
    try:
        client.get("https://example.com")
        assert False, "should have raised"
    except requests.exceptions.ConnectionError:
        pass
    assert calls["n"] == 3  # 初回 + リトライ2回
