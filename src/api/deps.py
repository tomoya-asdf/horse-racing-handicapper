"""API共通の依存・定数(認証セッション、管理者ガード、ジョブ表示名など)。

ルーター間で共有する状態(管理者セッション集合など)はここに一元化する。
ここは FastAPI の app には依存しない(ルーターから import される側)。
"""

from fastapi import HTTPException, Request

from src.common import jobs
from src.common.config import settings

ADMIN_COOKIE_NAME = "admin_session"
ADMIN_SESSION_SECONDS = 60 * 60 * 12
# プロセス内の管理者セッショントークン集合(login で追加・logout で破棄)。
ADMIN_SESSIONS: set[str] = set()

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


def _admin_configured() -> bool:
    return bool(settings.ADMIN_LOGIN_ID and settings.ADMIN_PASSWORD)


def _is_admin_request(request: Request) -> bool:
    token = request.cookies.get(ADMIN_COOKIE_NAME)
    return bool(token and token in ADMIN_SESSIONS)


def require_admin(request: Request) -> None:
    if not _admin_configured():
        raise HTTPException(status_code=503, detail="管理者ログインが設定されていません")
    if not _is_admin_request(request):
        raise HTTPException(status_code=401, detail="管理者ログインが必要です")
