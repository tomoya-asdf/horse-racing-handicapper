"""ジョブ実行・予約・スケジュールの API。"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func

from src.api.deps import BACKFILL_MAX_DAYS, JOB_LABELS, require_admin
from src.api.serializers import _job_to_dict, _reservation_to_dict
from src.common import jobs
from src.common.db import get_session
from src.common.dynamic_config import save_settings, scheduled_jobs_view
from src.common.models import JobRun
from src.common.timeutils import JST, now_jst

router = APIRouter()


@router.get("/api/jobs", dependencies=[Depends(require_admin)])
def list_jobs(limit: int = 50, offset: int = 0) -> dict:
    page_limit = min(max(limit, 1), 200)
    page_offset = max(offset, 0)
    session = get_session()
    try:
        jobs_total = session.query(func.count(JobRun.id)).scalar() or 0
        runs = (
            session.query(JobRun)
            .order_by(JobRun.created_at.desc().nullslast(), JobRun.id.desc())
            .offset(page_offset)
            .limit(page_limit)
            .all()
        )
        latest_jobs = []
        for job_name in jobs.ALL_JOBS:
            run = (
                session.query(JobRun)
                .filter(JobRun.job_name == job_name)
                .order_by(JobRun.created_at.desc().nullslast(), JobRun.id.desc())
                .first()
            )
            if run:
                latest_jobs.append(_job_to_dict(run))
        return {
            "jobs": [_job_to_dict(run) for run in runs],
            "jobs_total": jobs_total,
            "latest_jobs": latest_jobs,
            "scheduled_jobs": scheduled_jobs_view(),
            "reservations": [
                _reservation_to_dict(row) for row in jobs.list_reservations(limit=100)
            ],
        }
    finally:
        session.close()


@router.put("/api/jobs/schedule", dependencies=[Depends(require_admin)])
def update_job_schedule(values: dict) -> dict:
    try:
        updated = save_settings(values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"scheduled_jobs": updated["scheduled_jobs"]}


def _validate_backfill_params(body: dict) -> dict:
    try:
        start = datetime.strptime(str(body.get("start_date", "")), "%Y-%m-%d").date()
        end = datetime.strptime(str(body.get("end_date", "")), "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            status_code=400, detail="start_date / end_date をYYYY-MM-DDで指定してください"
        )
    if start > end:
        raise HTTPException(status_code=400, detail="開始日は終了日以前を指定してください")
    if end >= now_jst().date():
        raise HTTPException(
            status_code=400,
            detail="過去データ取得は昨日以前の日付専用です(当日以降は通常のデータ収集が対象)",
        )
    if (end - start).days + 1 > BACKFILL_MAX_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"一度に取得できるのは{BACKFILL_MAX_DAYS}日分までです(分割して実行してください)",
        )
    return {"start_date": start.isoformat(), "end_date": end.isoformat()}


def _validate_backtest_params(body: dict) -> dict:
    try:
        start = datetime.strptime(str(body.get("start_date", "")), "%Y-%m-%d").date()
        end = datetime.strptime(str(body.get("end_date", "")), "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            status_code=400, detail="start_date / end_date をYYYY-MM-DDで指定してください"
        )
    if start > end:
        raise HTTPException(status_code=400, detail="開始日は終了日以前を指定してください")
    if start > now_jst().date():
        raise HTTPException(status_code=400, detail="開始日は未来日を指定できません")
    return {"start_date": start.isoformat(), "end_date": end.isoformat()}


def _parse_reservation_run_at(value: object) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="実行日時を指定してください")
    try:
        run_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="実行日時の形式が不正です")
    if run_at.tzinfo is not None:
        run_at = run_at.astimezone(JST).replace(tzinfo=None)
    if run_at <= now_jst():
        raise HTTPException(status_code=400, detail="実行日時は未来の日時を指定してください")
    return run_at


def _validate_reservation_params(job_name: str, body: dict) -> dict | None:
    params = body.get("params")
    if job_name == jobs.BACKFILL:
        return _validate_backfill_params(params if isinstance(params, dict) else {})
    if job_name == jobs.BACKTEST:
        return _validate_backtest_params(params if isinstance(params, dict) else {})
    return params if isinstance(params, dict) and params else None


@router.post("/api/job-reservations", dependencies=[Depends(require_admin)])
def create_job_reservation(body: dict) -> dict:
    job_name = str(body.get("job_name", "")).strip()
    if job_name not in jobs.ALL_JOBS:
        raise HTTPException(status_code=400, detail=f"未対応のジョブです: {job_name}")
    run_at = _parse_reservation_run_at(body.get("run_at"))
    params = _validate_reservation_params(job_name, body)
    reservation = jobs.reserve(job_name, run_at, params)
    return _reservation_to_dict(reservation)


@router.post("/api/job-reservations/{reservation_id}/cancel", dependencies=[Depends(require_admin)])
def cancel_job_reservation(reservation_id: int) -> dict:
    if not jobs.cancel_reservation(reservation_id):
        raise HTTPException(status_code=409, detail="キャンセルできる予約が見つかりません")
    return {"cancelled": True, "id": reservation_id}


@router.post("/api/jobs/{job_name}/run", dependencies=[Depends(require_admin)])
def trigger_job(job_name: str, body: dict | None = None) -> dict:
    if job_name not in jobs.ALL_JOBS:
        raise HTTPException(status_code=400, detail=f"未対応のジョブです: {job_name}")
    params = None
    if job_name == jobs.BACKFILL:
        params = _validate_backfill_params(body or {})
    elif job_name == jobs.BACKTEST:
        params = _validate_backtest_params(body or {})
    result = jobs.enqueue(job_name, params)
    return {**result, "job_name": job_name, "label": JOB_LABELS[job_name]}


@router.get("/api/jobs/{run_id}", dependencies=[Depends(require_admin)])
def job_detail(run_id: int) -> dict:
    session = get_session()
    try:
        run = session.get(JobRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="job run not found")
        return _job_to_dict(run)
    finally:
        session.close()


@router.post("/api/jobs/{run_id}/stop", dependencies=[Depends(require_admin)])
def stop_job(run_id: int) -> dict:
    if not jobs.stop_queued(run_id):
        raise HTTPException(
            status_code=409,
            detail="停止できるのは実行待ち(queued)のジョブのみです。実行中ジョブは安全に中断できません。",
        )
    return {"stopped": True, "id": run_id}
