"""管理者ログイン関連のエンドポイント。"""

import secrets

from fastapi import APIRouter, HTTPException, Request, Response

from src.api.deps import (
    ADMIN_COOKIE_NAME,
    ADMIN_SESSION_SECONDS,
    ADMIN_SESSIONS,
    _admin_configured,
    _is_admin_request,
)
from src.common.config import settings

router = APIRouter()


@router.get("/api/auth/status")
def auth_status(request: Request) -> dict:
    return {"configured": _admin_configured(), "authenticated": _is_admin_request(request)}


@router.post("/api/auth/login")
def auth_login(values: dict, response: Response) -> dict:
    if not _admin_configured():
        raise HTTPException(status_code=503, detail="ADMIN_LOGIN_ID / ADMIN_PASSWORD を.envに設定してください")
    login_id = str(values.get("login_id", ""))
    password = str(values.get("password", ""))
    if not (
        secrets.compare_digest(login_id, settings.ADMIN_LOGIN_ID)
        and secrets.compare_digest(password, settings.ADMIN_PASSWORD)
    ):
        raise HTTPException(status_code=401, detail="ログインIDまたはパスワードが違います")
    token = secrets.token_urlsafe(32)
    ADMIN_SESSIONS.add(token)
    response.set_cookie(
        ADMIN_COOKIE_NAME,
        token,
        max_age=ADMIN_SESSION_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return {"authenticated": True}


@router.post("/api/auth/logout")
def auth_logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(ADMIN_COOKIE_NAME)
    if token:
        ADMIN_SESSIONS.discard(token)
    response.delete_cookie(ADMIN_COOKIE_NAME)
    return {"authenticated": False}
