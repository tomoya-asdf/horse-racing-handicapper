"""WebUI用のバックエンドAPI(FastAPI)。

システム全体の状況・履歴の参照、ジョブの手動実行、設定変更を提供する。
ジョブの実行自体は行わず、job_runs テーブルへの登録のみ行い、
担当サービス(collector/predictor)がポーリングして実行する。
ビルド済みのフロントエンド(webui/dist)も同じプロセスから配信する。

エンドポイントは責務ごとに ``src/api/routers/`` 配下へ分割し、ここでは FastAPI
アプリの生成・ルーター登録・静的配信のみを行う(uvicorn の起動点は ``src.api.main:app``)。
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.api.routers import (
    auth,
    bets,
    horses,
    jobs,
    models,
    overview,
    people,
    races,
    settings,
    system,
)
from src.common.db import init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="競馬予測AI 管理API")


@app.on_event("startup")
def startup() -> None:
    init_db()


# ルーター登録(API)。静的配信より先に登録し、/api/* を確実に拾う。
for _router_module in (
    auth,
    overview,
    races,
    horses,
    people,
    models,
    bets,
    jobs,
    settings,
    system,
):
    app.include_router(_router_module.router)


# ビルド済みフロントエンドの配信(/api 以外のパス)。catch-all のため最後に登録する。
WEBUI_DIST = Path(__file__).resolve().parents[2] / "webui" / "dist"
if WEBUI_DIST.exists():

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEBUI_DIST / "index.html")

    @app.get("/horses/{horse_id}")
    def horse_page(horse_id: str) -> FileResponse:
        return FileResponse(WEBUI_DIST / "index.html")

    @app.get("/jockeys/{jockey_id}")
    def jockey_page(jockey_id: str) -> FileResponse:
        return FileResponse(WEBUI_DIST / "index.html")

    @app.get("/trainers/{trainer_id}")
    def trainer_page(trainer_id: str) -> FileResponse:
        return FileResponse(WEBUI_DIST / "index.html")

    @app.get("/models")
    def models_page() -> FileResponse:
        return FileResponse(WEBUI_DIST / "index.html")

    @app.get("/models/{version}")
    def model_page(version: str) -> FileResponse:
        return FileResponse(WEBUI_DIST / "index.html")

    app.mount("/", StaticFiles(directory=WEBUI_DIST, html=True), name="webui")
else:
    logger.warning("webui/dist が見つかりません。フロントエンドは配信されません。")
