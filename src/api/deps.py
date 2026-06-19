"""API共通の依存・定数(認証セッション、管理者ガード、ジョブ表示名など)。

ルーター間で共有する状態(管理者セッション、ログイン試行回数など)はここに一元化する。
ここは FastAPI の app には依存しない(ルーターから import される側)。
"""

import threading
import time

from fastapi import HTTPException, Request

from src.common import jobs
from src.common.config import settings

ADMIN_COOKIE_NAME = "admin_session"
ADMIN_SESSION_SECONDS = settings.ADMIN_SESSION_SECONDS

# プロセス内の管理者セッション。token -> 有効期限(epoch秒)。
# 有効期限を持たせることで、盗まれたトークンも期限切れで無効になり、
# 期限切れエントリは参照時に掃除してメモリリークを防ぐ。
_ADMIN_SESSIONS: dict[str, float] = {}
# ログイン試行のレート制限。クライアント識別子 -> 直近の失敗時刻リスト。
_LOGIN_FAILURES: dict[str, list[float]] = {}
_LOCK = threading.Lock()

BACKFILL_MAX_DAYS = 31

JOB_LABELS = {
    jobs.COLLECT: "データ収集",
    jobs.BACKFILL: "過去データ取得",
    jobs.COLLECT_HORSES: "馬過去成績収集",
    jobs.PREDICT: "AI予想",
    jobs.BET_DECIDE: "賭け対象決定",
    jobs.SETTLE: "決済",
    jobs.TRAIN: "モデル学習",
    jobs.BACKTEST: "回収率バックテスト",
}


def admin_configured() -> bool:
    return bool(settings.ADMIN_LOGIN_ID and settings.ADMIN_PASSWORD)


def create_admin_session() -> str:
    """新しい管理セッショントークンを発行し、有効期限付きで保持する。"""
    import secrets

    token = secrets.token_urlsafe(32)
    with _LOCK:
        _ADMIN_SESSIONS[token] = time.time() + ADMIN_SESSION_SECONDS
        _prune_sessions_locked()
    return token


def destroy_admin_session(token: str | None) -> None:
    if not token:
        return
    with _LOCK:
        _ADMIN_SESSIONS.pop(token, None)


def _prune_sessions_locked() -> None:
    now = time.time()
    expired = [token for token, expiry in _ADMIN_SESSIONS.items() if expiry <= now]
    for token in expired:
        _ADMIN_SESSIONS.pop(token, None)


def is_admin_request(request: Request) -> bool:
    token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not token:
        return False
    now = time.time()
    with _LOCK:
        expiry = _ADMIN_SESSIONS.get(token)
        if expiry is None:
            return False
        if expiry <= now:
            _ADMIN_SESSIONS.pop(token, None)
            return False
        return True


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def login_rate_limited(request: Request) -> bool:
    """直近ウィンドウ内のログイン失敗が上限を超えていれば True。"""
    now = time.time()
    window = settings.ADMIN_LOGIN_WINDOW_SECONDS
    key = _client_key(request)
    with _LOCK:
        attempts = [t for t in _LOGIN_FAILURES.get(key, []) if now - t < window]
        _LOGIN_FAILURES[key] = attempts
        return len(attempts) >= settings.ADMIN_LOGIN_MAX_ATTEMPTS


def register_login_failure(request: Request) -> None:
    now = time.time()
    key = _client_key(request)
    with _LOCK:
        _LOGIN_FAILURES.setdefault(key, []).append(now)


def reset_login_failures(request: Request) -> None:
    with _LOCK:
        _LOGIN_FAILURES.pop(_client_key(request), None)


def require_admin(request: Request) -> None:
    if not admin_configured():
        raise HTTPException(status_code=503, detail="管理者ログインが設定されていません")
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="管理者ログインが必要です")
