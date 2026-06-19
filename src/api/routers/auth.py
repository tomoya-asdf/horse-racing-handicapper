"""管理者ログイン関連のエンドポイント。"""

import secrets

from fastapi import APIRouter, HTTPException, Request, Response

from src.api.deps import (
    ADMIN_COOKIE_NAME,
    ADMIN_SESSION_SECONDS,
    admin_configured,
    create_admin_session,
    destroy_admin_session,
    is_admin_request,
    login_rate_limited,
    register_login_failure,
    reset_login_failures,
)
from src.common.config import settings

router = APIRouter()


@router.get("/api/auth/status")
def auth_status(request: Request) -> dict:
    return {"configured": admin_configured(), "authenticated": is_admin_request(request)}


@router.post("/api/auth/login")
def auth_login(values: dict, request: Request, response: Response) -> dict:
    if not admin_configured():
        raise HTTPException(status_code=503, detail="ADMIN_LOGIN_ID / ADMIN_PASSWORD を.envに設定してください")
    if login_rate_limited(request):
        raise HTTPException(
            status_code=429,
            detail="ログイン試行が多すぎます。しばらく待ってから再度お試しください。",
        )
    login_id = str(values.get("login_id", ""))
    password = str(values.get("password", ""))
    if not (
        secrets.compare_digest(login_id, settings.ADMIN_LOGIN_ID)
        and secrets.compare_digest(password, settings.ADMIN_PASSWORD)
    ):
        register_login_failure(request)
        raise HTTPException(status_code=401, detail="ログインIDまたはパスワードが違います")
    reset_login_failures(request)
    token = create_admin_session()
    response.set_cookie(
        ADMIN_COOKIE_NAME,
        token,
        max_age=ADMIN_SESSION_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.ADMIN_COOKIE_SECURE,
    )
    return {"authenticated": True}


@router.post("/api/auth/logout")
def auth_logout(request: Request, response: Response) -> dict:
    destroy_admin_session(request.cookies.get(ADMIN_COOKIE_NAME))
    response.delete_cookie(ADMIN_COOKIE_NAME)
    return {"authenticated": False}
