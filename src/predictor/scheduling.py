"""予測サービスのスケジュール判定。

各 ``_scheduled_*`` は1分間隔で呼ばれ、動的設定(有効/無効・間隔・曜日・時刻)と
発走時刻からの逆算で実行要否を判断し、due なら対応する ``_run_*`` を起動する。
"""

import logging
from datetime import timedelta

from src.common import jobs
from src.common.config import settings
from src.common.db import session_scope
from src.common.dynamic_config import load_scheduled_job_config
from src.common.models import Bet, BetStatus, Entry, Race
from src.common.timeutils import now_jst
from src.predictor.tasks import run_bet_decide, run_predict, run_settle, run_train

logger = logging.getLogger(__name__)


def _next_bet_decide_due_at(lead_minutes: int):
    with session_scope() as session:
        race = (
            session.query(Race)
            .filter(
                Race.start_time.isnot(None),
                Race.start_time > now_jst(),
                Race.entries.any(),
                ~Race.entries.any(Entry.finish_position.isnot(None)),
            )
            .order_by(Race.start_time)
            .first()
        )
        if race is None or race.start_time is None:
            return None
        return race.start_time - timedelta(minutes=lead_minutes)


def _next_settle_due_at(delay_minutes: int):
    with session_scope() as session:
        row = (
            session.query(Race.start_time)
            .join(Bet, Bet.race_id == Race.id)
            .filter(
                Bet.is_settled.is_(False),
                Bet.status == BetStatus.PLACED.value,
                Race.start_time.isnot(None),
            )
            .order_by(Race.start_time)
            .first()
        )
        if row is None or row[0] is None:
            return None
        return row[0] + timedelta(minutes=delay_minutes)


def scheduled_predict() -> None:
    config = load_scheduled_job_config(jobs.PREDICT)
    if config is None or not config.enabled:
        return
    if not jobs.scheduled_run_due(
        jobs.PREDICT,
        config.interval_minutes,
        weekdays=config.weekdays,
        exact_time=config.exact_time,
    ):
        return
    jobs.run_scheduled(jobs.PREDICT, run_predict)


def scheduled_bet_decide() -> None:
    config = load_scheduled_job_config(jobs.BET_DECIDE)
    if config is None or not config.enabled:
        return
    due_at = None
    if not config.exact_time:
        before_start_minutes = (
            config.before_start_minutes
            if config.before_start_minutes is not None
            else settings.BET_DECISION_LEAD_MINUTES
        )
        due_at = _next_bet_decide_due_at(before_start_minutes)
        if due_at is None:
            return
    if not jobs.scheduled_run_due(
        jobs.BET_DECIDE,
        config.interval_minutes,
        due_at=due_at,
        weekdays=config.weekdays,
        exact_time=config.exact_time,
    ):
        return
    jobs.run_scheduled(jobs.BET_DECIDE, run_bet_decide)


def scheduled_settle() -> None:
    config = load_scheduled_job_config(jobs.SETTLE)
    if config is None or not config.enabled:
        return
    due_at = None
    if not config.exact_time:
        after_start_minutes = (
            config.after_start_minutes
            if config.after_start_minutes is not None
            else settings.SETTLE_DELAY_MINUTES
        )
        due_at = _next_settle_due_at(after_start_minutes)
        if due_at is None:
            return
    if not jobs.scheduled_run_due(
        jobs.SETTLE,
        config.interval_minutes,
        due_at=due_at,
        weekdays=config.weekdays,
        exact_time=config.exact_time,
    ):
        return
    jobs.run_scheduled(jobs.SETTLE, run_settle)


def scheduled_train() -> None:
    config = load_scheduled_job_config(jobs.TRAIN)
    if config is None or not config.enabled:
        return
    if not jobs.scheduled_run_due(
        jobs.TRAIN,
        config.interval_minutes,
        weekdays=config.weekdays,
        exact_time=config.exact_time,
    ):
        return
    jobs.run_scheduled(jobs.TRAIN, run_train)
