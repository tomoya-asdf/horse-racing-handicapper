"""管理セッション(有効期限)とログインのレート制限のテスト。"""

import time
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")  # deps は fastapi に依存するため、無い環境ではスキップ

from src.api import deps  # noqa: E402


def _request(cookie_token=None, host="10.0.0.1"):
    cookies = {deps.ADMIN_COOKIE_NAME: cookie_token} if cookie_token else {}
    return SimpleNamespace(cookies=cookies, client=SimpleNamespace(host=host))


@pytest.fixture(autouse=True)
def _clear_state():
    deps._ADMIN_SESSIONS.clear()
    deps._LOGIN_FAILURES.clear()
    yield
    deps._ADMIN_SESSIONS.clear()
    deps._LOGIN_FAILURES.clear()


def test_session_roundtrip():
    token = deps.create_admin_session()
    assert deps.is_admin_request(_request(token)) is True
    deps.destroy_admin_session(token)
    assert deps.is_admin_request(_request(token)) is False


def test_no_cookie_is_not_admin():
    assert deps.is_admin_request(_request(None)) is False


def test_expired_session_rejected_and_pruned():
    token = deps.create_admin_session()
    # 有効期限を過去に設定する → 無効、かつ集合から掃除される
    deps._ADMIN_SESSIONS[token] = time.time() - 1
    assert deps.is_admin_request(_request(token)) is False
    assert token not in deps._ADMIN_SESSIONS


def test_login_rate_limit(monkeypatch):
    monkeypatch.setattr(deps.settings, "ADMIN_LOGIN_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(deps.settings, "ADMIN_LOGIN_WINDOW_SECONDS", 300)
    req = _request(host="1.2.3.4")
    assert deps.login_rate_limited(req) is False
    for _ in range(3):
        deps.register_login_failure(req)
    assert deps.login_rate_limited(req) is True
    # 別クライアントは独立してカウントされる
    assert deps.login_rate_limited(_request(host="5.6.7.8")) is False
    # 成功でリセットされる
    deps.reset_login_failures(req)
    assert deps.login_rate_limited(req) is False
