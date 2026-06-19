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
from datetime import datetime, timedelta
from typing import Callable

from src.common.db import session_scope
from src.common.models import JobReservation, JobRun, JobStatus, JobTrigger
from src.common.timeutils import now_jst

logger = logging.getLogger(__name__)

Handler = Callable[[dict], "str | None"]

# ジョブ名と担当サービス
COLLECT = "collect"  # collector
BACKFILL = "backfill"  # collector
COLLECT_HORSES = "collect_horses"  # collector(馬の過去成績収集)
PREDICT = "predict"  # predictor
BET_DECIDE = "bet_decide"  # predictor
SETTLE = "settle"  # predictor
TRAIN = "train"  # predictor
BACKTEST = "backtest"  # predictor(回収率バックテスト)
ALL_JOBS = (
    COLLECT,
    BACKFILL,
    COLLECT_HORSES,
    PREDICT,
    BET_DECIDE,
    SETTLE,
    TRAIN,
    BACKTEST,
)

POLL_INTERVAL_SECONDS = 5

# これより古いrunning行は異常終了の残骸とみなし、新規実行をブロックしない
STALE_RUNNING_MINUTES = 60
RESERVATION_PENDING = "pending"
RESERVATION_QUEUED = "queued"
RESERVATION_CANCELLED = "cancelled"


def _today_exact_due_at(exact_time: str):
    now = now_jst()
    hour, minute = (int(part) for part in exact_time.split(":", 1))
    return datetime.combine(now.date(), datetime.min.time()).replace(
        hour=hour,
        minute=minute,
        tzinfo=now.tzinfo,
    )


def scheduled_run_due(
    job_name: str, interval_minutes: int | None, due_at=None, weekdays=None, exact_time: str | None = None
) -> bool:
    """Return True when a scheduled job is enabled to start a new run now.

    ``weekdays`` を渡すと、当日の曜日(月=0〜日=6)が含まれないとき実行しない。
    """
    now = now_jst()
    if weekdays is not None and now.weekday() not in weekdays:
        return False
    exact_due_at = None
    if exact_time:
        exact_due_at = _today_exact_due_at(exact_time)
        if now < exact_due_at:
            return False
    if due_at is not None and due_at > now:
        return False

    with session_scope() as session:
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
        if exact_due_at is not None:
            return latest.started_at < exact_due_at
        if interval_minutes is None:
            return False
        return latest.started_at <= now - timedelta(minutes=interval_minutes)


def enqueue(job_name: str, params: dict | None = None) -> dict:
    """ジョブの実行を依頼する。同名ジョブが実行待ち/実行中なら新規追加しない。"""
    with session_scope() as session:
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


def reserve(job_name: str, run_at, params: dict | None = None) -> dict:
    """指定日時に1回だけジョブを投入する予約を作成する。"""
    with session_scope() as session:
        reservation = JobReservation(
            job_name=job_name,
            run_at=run_at,
            params=json.dumps(params, ensure_ascii=False) if params else None,
            status=RESERVATION_PENDING,
        )
        session.add(reservation)
        session.commit()
        return _reservation_to_dict(reservation)


def list_reservations(limit: int = 100) -> list[dict]:
    with session_scope() as session:
        rows = (
            session.query(JobReservation)
            .order_by(JobReservation.run_at.desc(), JobReservation.id.desc())
            .limit(min(max(limit, 1), 300))
            .all()
        )
        return [_reservation_to_dict(row) for row in rows]


def cancel_reservation(reservation_id: int) -> bool:
    with session_scope() as session:
        reservation = session.get(JobReservation, reservation_id)
        if reservation is None or reservation.status != RESERVATION_PENDING:
            return False
        reservation.status = RESERVATION_CANCELLED
        reservation.cancelled_at = now_jst()
        session.commit()
        return True


def enqueue_due_reservations(job_names: list[str]) -> int:
    """期限到来した予約を通常の queued job_runs に変換する。"""
    now = now_jst()
    queued_count = 0
    with session_scope() as session:
        blocked_names = {
            row.job_name
            for row in session.query(JobRun.job_name)
            .filter(
                JobRun.job_name.in_(job_names),
                JobRun.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
                JobRun.created_at > now - timedelta(minutes=STALE_RUNNING_MINUTES),
            )
            .all()
        }
        reservations = (
            session.query(JobReservation)
            .filter(
                JobReservation.status == RESERVATION_PENDING,
                JobReservation.job_name.in_(job_names),
                JobReservation.run_at <= now,
            )
            .order_by(JobReservation.run_at, JobReservation.id)
            .all()
        )
        claimed_names: set[str] = set()
        for reservation in reservations:
            if reservation.job_name in blocked_names or reservation.job_name in claimed_names:
                continue
            run = JobRun(
                job_name=reservation.job_name,
                trigger=JobTrigger.RESERVED.value,
                status=JobStatus.QUEUED.value,
                params=reservation.params,
            )
            session.add(run)
            session.flush()
            reservation.status = RESERVATION_QUEUED
            reservation.queued_run_id = run.id
            reservation.queued_at = now
            claimed_names.add(reservation.job_name)
            queued_count += 1
        session.commit()
    return queued_count


def stop_queued(run_id: int) -> bool:
    """queuedの手動ジョブを停止する。実行中ジョブは安全に中断できないため対象外。"""
    with session_scope() as session:
        run = session.get(JobRun, run_id)
        if run is None or run.status != JobStatus.QUEUED.value:
            return False
        run.status = JobStatus.FAILED.value
        run.detail = "管理者により実行前に停止されました"
        run.finished_at = now_jst()
        session.commit()
        return True


def run_scheduled(job_name: str, func: Handler) -> None:
    """スケジュール実行のエントリポイント。履歴を記録しつつジョブを実行する。"""
    run_id = _create_running(job_name, JobTrigger.SCHEDULED.value)
    _execute(run_id, job_name, func, {})


def process_queued(handlers: dict[str, Handler]) -> None:
    """queuedのジョブをclaimして実行する(各サービスのポーリングジョブから呼ぶ)。"""
    with session_scope() as session:
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

    for run_id, job_name, params in claimed:
        _execute(run_id, job_name, handlers[job_name], params)


def recover_stale(job_names: list[str]) -> None:
    """サービス起動時に、前回の異常終了で残ったrunning行をfailedにする。"""
    with session_scope() as session:
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


def _parse_params(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except ValueError:
        return {}


def _reservation_to_dict(reservation: JobReservation) -> dict:
    return {
        "id": reservation.id,
        "job_name": reservation.job_name,
        "run_at": reservation.run_at.isoformat() if reservation.run_at else None,
        "params": reservation.params,
        "status": reservation.status,
        "queued_run_id": reservation.queued_run_id,
        "created_at": reservation.created_at.isoformat() if reservation.created_at else None,
        "queued_at": reservation.queued_at.isoformat() if reservation.queued_at else None,
        "cancelled_at": (
            reservation.cancelled_at.isoformat() if reservation.cancelled_at else None
        ),
    }


def _create_running(job_name: str, trigger: str) -> int:
    with session_scope() as session:
        run = JobRun(
            job_name=job_name,
            trigger=trigger,
            status=JobStatus.RUNNING.value,
            started_at=now_jst(),
        )
        session.add(run)
        session.commit()
        return run.id


def _finish(run_id: int, status: str, detail: str | None) -> None:
    with session_scope() as session:
        run = session.get(JobRun, run_id)
        if run is None:
            return
        run.status = status
        run.detail = (detail or "")[:2000]
        run.finished_at = now_jst()
        session.commit()


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
