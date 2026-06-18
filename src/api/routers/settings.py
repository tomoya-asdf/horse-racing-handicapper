"""アプリ設定の参照・保存 API。"""

from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import require_admin
from src.common.dynamic_config import get_settings_view, save_settings

router = APIRouter()


@router.get("/api/settings", dependencies=[Depends(require_admin)])
def read_settings() -> dict:
    return get_settings_view()


@router.put("/api/settings", dependencies=[Depends(require_admin)])
def update_settings(values: dict) -> dict:
    try:
        return save_settings(values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
