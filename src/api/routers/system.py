"""システム操作 API(再起動・バージョン・デプロイ依頼)。

アップデート/デプロイ/再起動は webui コンテナからは行わず、ホスト側の常駐エージェント
(scripts/deploy_agent.sh / deploy_agent.ps1)が担当する。両者は共有ボリューム ./data 上の
JSON でやりとりする(webui に docker.sock を渡さないための分離)。
  - deploy_status.json  : エージェントが書き込む現在の状態(バージョン/更新有無/進捗)
  - deploy_request.json : webui が書き込むデプロイ依頼。エージェントが処理後に削除する
  - restart_request.json: webui が書き込む再起動依頼。エージェントが処理後に削除する
"""

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import require_admin
from src.common import jobs
from src.common.db import session_scope
from src.common.models import JobRun, JobStatus
from src.common.timeutils import now_jst

router = APIRouter()

_DEPLOY_STATUS_FILE = Path("/app/data/deploy_status.json")
_DEPLOY_REQUEST_FILE = Path("/app/data/deploy_request.json")
_RESTART_REQUEST_FILE = Path("/app/data/restart_request.json")


def _read_deploy_status() -> dict:
    # PowerShell(Windowsホスト)が書く状態ファイルはBOM付きUTF-8になりうるため
    # utf-8-sig で読み、BOMがあっても無くても解釈できるようにする
    try:
        return json.loads(_DEPLOY_STATUS_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


@router.post("/api/system/restart", dependencies=[Depends(require_admin)])
def restart_system() -> dict:
    """コンテナ再起動をホストエージェントへ依頼する。

    デプロイと同じく webui コンテナからは docker を直接操作せず、共有ボリューム上の
    依頼ファイルを書いてホスト側エージェントに実行させる(docker.sock 非共有のため)。
    """
    status = _read_deploy_status()
    if not status:
        raise HTTPException(
            status_code=409,
            detail=(
                "デプロイエージェントが検出できません(状態ファイルがありません)。"
                "ホスト側でデプロイエージェントを起動してください"
                "(Linux: scripts/deploy_agent.sh / Windows: scripts/deploy_agent.ps1)。"
            ),
        )
    if status.get("state") in ("requested", "running"):
        raise HTTPException(status_code=409, detail="デプロイ/再起動を処理中です。完了後に再度お試しください。")

    try:
        _RESTART_REQUEST_FILE.write_text(
            json.dumps({"requested_at": now_jst().isoformat()}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"再起動依頼の書き込みに失敗しました: {exc}")
    return {"requested": True}


@router.get("/api/system/version")
def system_version() -> dict:
    """稼働中バージョン・更新有無・デプロイ進捗を返す(ホストエージェントが書く状態ファイル)。"""
    status = _read_deploy_status()
    return {
        "available": bool(status),  # エージェントが状態を書けているか
        "current_sha": status.get("current_sha"),
        "current_ref": status.get("current_ref"),
        "remote_sha": status.get("remote_sha"),
        "update_available": bool(status.get("update_available")),
        "last_checked_at": status.get("last_checked_at"),
        "state": status.get("state"),  # idle / requested / running / success / failed
        "last_deploy_at": status.get("last_deploy_at"),
        "last_deploy_result": status.get("last_deploy_result"),
        "message": status.get("message"),
        "agent_seen_at": status.get("agent_seen_at"),
    }


@router.post("/api/system/deploy", dependencies=[Depends(require_admin)])
def request_deploy() -> dict:
    """デプロイ(git pull + build + 再起動)をホストエージェントへ依頼する。

    実行自体はエージェントが行う。実弾投票を中断しないよう、bet_decide / settle が
    実行中のときは依頼を拒否する。
    """
    with session_scope() as session:
        busy = (
            session.query(JobRun.id)
            .filter(
                JobRun.job_name.in_([jobs.BET_DECIDE, jobs.SETTLE]),
                JobRun.status == JobStatus.RUNNING.value,
            )
            .first()
        )
    if busy is not None:
        raise HTTPException(
            status_code=409,
            detail="買い目判定/精算ジョブの実行中はデプロイできません。完了後に再度お試しください。",
        )

    status = _read_deploy_status()
    if status.get("state") in ("requested", "running"):
        raise HTTPException(status_code=409, detail="すでにデプロイ依頼を処理中です。")
    if not status:
        raise HTTPException(
            status_code=409,
            detail=(
                "デプロイエージェントが検出できません(状態ファイルがありません)。"
                "ホスト側でデプロイエージェントを起動してください"
                "(Linux: scripts/deploy_agent.sh / Windows: scripts/deploy_agent.ps1)。"
            ),
        )

    try:
        _DEPLOY_REQUEST_FILE.write_text(
            json.dumps({"requested_at": now_jst().isoformat()}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"デプロイ依頼の書き込みに失敗しました: {exc}")
    return {"requested": True}
