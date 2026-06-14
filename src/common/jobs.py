"""ジョブの手動実行(WebUI起点)と実行履歴の管理。

WebUI(API)は ``enqueue()`` で job_runs に status=queued の行を入れるだけで、
実際の実行は担当サービスが行う(collectorはcollect/backfill、predictorは
predict/bet_decide/settle/train)。各サービスは ``process_queued()`` を数秒間隔でポーリングし、
queuedの行をclaimして実行する。スケジュール実行は ``run_scheduled()`` で同じ
テーブルに記録するため、WebUIから手動・自動を問わず全実行履歴を確認できる。

ハンドラはいずれも ``func(params: dict) -> str | None`` のシグネチャを取る。
``params`` はWebUIから渡された引数(backfillの日付範囲など)で、無ければ空dict。
"""

import json
import logging
import traceback
from datetime import timedelta
from typing import Callable

from src.common.db import get_session
from src.common.models import JobRun, JobStatus, JobTrigger
from src.common.timeutils import now_jst

logger = logging.getLogger(__name__)

Handler = Callable[[dict], "str | None"]

# ジョブ名と担当サービス
COLLECT = "collect"  # collector
BACKFILL = "backfill"  # collector
COLLECT_HORSES = "collect_horses"  # collector(馬の過去成績収集)
COLLECT_JOCKEYS = "collect_jockeys"  # collector(騎手の過去成績収集)
COLLECT_TRAINERS = "collect_trainers"  # collector(調教師の過去成績収集)
PREDICT = "predict"  # predictor
BET_DECIDE = "bet_decide"  # predictor
SETTLE = "settle"  # predictor
TRAIN = "train"  # predictor
BACKTEST = "backtest"  # predictor(回収率バックテスト)
ALL_JOBS = (
    COLLECT,
    BACKFILL,
    COLLECT_HORSES,
    COLLECT_JOCKEYS,
    COLLECT_TRAINERS,
    PREDICT,
    BET_DECIDE,
    SETTLE,
    TRAIN,
    BACKTEST,
)

POLL_INTERVAL_SECONDS = 5

# これより古いrunning行は異常終了の残骸とみなし、新規実行をブロックしない
STALE_RUNNING_MINUTES = 60


def scheduled_run_due(
    job_name: str, interval_minutes: int, due_at=None, weekdays=None
) -> bool:
    """Return True when a scheduled job is enabled to start a new run now.

    ``weekdays`` を渡すと、当日の曜日(月=0〜日=6)が含まれないとき実行しない。
    """
    now = now_jst()
    if weekdays is not None and now.weekday() not in weekdays:
        return False
    if due_at is not None and due_at > now:
        return False

    session = get_session()
    try:
        running = (
            session.query(JobRun.id)
            .filter(
                JobRun.job_name == job_name,
                JobRun.status == JobStatus.RUNNING.value,
                JobRun.started_at > now - timedelta(minutes=STALE_RUNNING_MINUTES),
            )
            .first()
        )
        if running is not None:
            return False

        latest = (
            session.query(JobRun)
            .filter(JobRun.job_name == job_name, JobRun.trigger == JobTrigger.SCHEDULED.value)
            .order_by(JobRun.started_at.desc().nullslast(), JobRun.created_at.desc())
            .first()
        )
        if latest is None or latest.started_at is None:
            return True
        return latest.started_at <= now - timedelta(minutes=interval_minutes)
    finally:
        session.close()


def enqueue(job_name: str, params: dict | None = None) -> dict:
    """ジョブの実行を依頼する。同名ジョブが実行待ち/実行中なら新規追加しない。"""
    session = get_session()
    try:
        existing = (
            session.query(JobRun)
            .filter(
                JobRun.job_name == job_name,
                JobRun.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
                JobRun.created_at > now_jst() - timedelta(minutes=STALE_RUNNING_MINUTES),
            )
            .first()
        )
        if existing is not None:
            return {"id": existing.id, "status": existing.status, "queued": False}

        run = JobRun(
            job_name=job_name,
            trigger=JobTrigger.MANUAL.value,
            status=JobStatus.QUEUED.value,
            params=json.dumps(params) if params else None,
        )
        session.add(run)
        session.commit()
        return {"id": run.id, "status": run.status, "queued": True}
    finally:
        session.close()


def stop_queued(run_id: int) -> bool:
    """queuedの手動ジョブを停止する。実行中ジョブは安全に中断できないため対象外。"""
    session = get_session()
    try:
        run = session.get(JobRun, run_id)
        if run is None or run.status != JobStatus.QUEUED.value:
            return False
        run.status = JobStatus.FAILED.value
        run.detail = "管理者により実行前に停止されました"
        run.finished_at = now_jst()
        session.commit()
        return True
    finally:
        session.close()


def run_scheduled(job_name: str, func: Handler) -> None:
    """スケジュール実行のエントリポイント。履歴を記録しつつジョブを実行する。"""
    run_id = _create_running(job_name, JobTrigger.SCHEDULED.value)
    _execute(run_id, job_name, func, {})


def process_queued(handlers: dict[str, Handler]) -> None:
    """queuedのジョブをclaimして実行する(各サービスのポーリングジョブから呼ぶ)。"""
    session = get_session()
    try:
        running_names = {
            row.job_name
            for row in session.query(JobRun.job_name)
            .filter(
                JobRun.status == JobStatus.RUNNING.value,
                JobRun.job_name.in_(list(handlers)),
                JobRun.started_at > now_jst() - timedelta(minutes=STALE_RUNNING_MINUTES),
            )
            .all()
        }

        claimed: list[tuple[int, str, dict]] = []
        queued = (
            session.query(JobRun)
            .filter(
                JobRun.status == JobStatus.QUEUED.value,
                JobRun.job_name.in_(list(handlers)),
            )
            .order_by(JobRun.created_at)
            .all()
        )
        for run in queued:
            if run.job_name in running_names or any(
                name == run.job_name for _, name, _ in claimed
            ):
                continue  # 同名ジョブの同時実行はしない(次のポーリングで実行)
            run.status = JobStatus.RUNNING.value
            run.started_at = now_jst()
            claimed.append((run.id, run.job_name, _parse_params(run.params)))
        session.commit()
    finally:
        session.close()

    for run_id, job_name, params in claimed:
        _execute(run_id, job_name, handlers[job_name], params)


def recover_stale(job_names: list[str]) -> None:
    """サービス起動時に、前回の異常終了で残ったrunning行をfailedにする。"""
    session = get_session()
    try:
        stale_runs = (
            session.query(JobRun)
            .filter(
                JobRun.status == JobStatus.RUNNING.value,
                JobRun.job_name.in_(job_names),
            )
            .all()
        )
        for run in stale_runs:
            run.status = JobStatus.FAILED.value
            run.detail = "サービスの再起動により中断されました"
            run.finished_at = now_jst()
        session.commit()
    finally:
        session.close()


def _parse_params(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except ValueError:
        return {}


def _create_running(job_name: str, trigger: str) -> int:
    session = get_session()
    try:
        run = JobRun(
            job_name=job_name,
            trigger=trigger,
            status=JobStatus.RUNNING.value,
            started_at=now_jst(),
        )
        session.add(run)
        session.commit()
        return run.id
    finally:
        session.close()


def _finish(run_id: int, status: str, detail: str | None) -> None:
    session = get_session()
    try:
        run = session.get(JobRun, run_id)
        if run is None:
            return
        run.status = status
        run.detail = (detail or "")[:2000]
        run.finished_at = now_jst()
        session.commit()
    finally:
        session.close()


def _execute(run_id: int, job_name: str, func: Handler, params: dict) -> None:
    logger.info("job started: %s (run_id=%s)", job_name, run_id)
    try:
        detail = func(params)
    except Exception:
        logger.exception("job failed: %s (run_id=%s)", job_name, run_id)
        _finish(run_id, JobStatus.FAILED.value, traceback.format_exc(limit=5))
        return
    _finish(run_id, JobStatus.SUCCESS.value, detail)
    logger.info("job finished: %s (run_id=%s) %s", job_name, run_id, detail or "")
