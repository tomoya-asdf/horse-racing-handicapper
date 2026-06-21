"""収集サービスのスケジューラ兼ジョブ配線。

業務ロジックは責務ごとのモジュールに分離している:

- calendar_store : 開催カレンダーの取得・保存(collect_kaisai_dates)
- races_store    : レース・出走表のDB反映、確定結果の取り込み
- horse_results  : races 起点の馬の過去成績・血統収集

本モジュールはジョブ実行関数(_run_*)・スケジュール判定(_scheduled_*)・
キュー処理を組み立て、APScheduler で定期実行するだけに留める。
"""

import logging
from datetime import timedelta

from apscheduler.schedulers.blocking import BlockingScheduler

from src.collector import scraper
from src.collector.calendar_store import collect_kaisai_dates
from src.collector.horse_results import update_horse_results
from src.collector.races_store import update_finished_results, upsert_races
from src.common import jobs
from src.common.config import settings
from src.common.db import init_db
from src.common.dynamic_config import load_scheduled_job_config
from src.common.scheduling_priority import betting_priority_active
from src.common.timeutils import now_jst

logging.basicConfig(level=logging.INFO)
# 5秒間隔のジョブポーリングがINFOログを埋め尽くすため、APSchedulerのログは抑制する
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def _run_collect(params: dict) -> str:
    # JRAは主に土日開催のため、当日だけでなく数日先まで収集する。開催カレンダーを
    # 先に取得・保存し、開催日にだけ出馬表を取りに行くことで、開催の無い日への
    # リクエストを省く(netkeiba負荷軽減)。
    today = now_jst().date()
    end = today + timedelta(days=settings.COLLECT_DAYS_AHEAD)
    kaisai = collect_kaisai_dates(today, end)
    total = 0
    for offset in range(settings.COLLECT_DAYS_AHEAD + 1):
        target = today + timedelta(days=offset)
        if target not in kaisai:
            continue
        races = scraper.fetch_upcoming_races(target)
        upsert_races(races)
        total += len(races)
    updated = update_finished_results()
    return (
        f"取得レース={total}件({today}〜{end}, 開催{len(kaisai)}日), "
        f"結果反映={updated}件"
    )


def _collect_races_limit(params: dict) -> int:
    """1回の収集で処理する未収集レース数の上限(params.limit 優先、既定は設定値)。"""
    default_limit = settings.RESULTS_RACES_PER_RUN
    try:
        return int(params.get("limit", default_limit)) if params else default_limit
    except (TypeError, ValueError):
        return default_limit


def _run_collect_horses(params: dict) -> str:
    """未収集レースの出走馬について、過去成績と5代血統をまとめて収集する手動ジョブ。"""
    limit = _collect_races_limit(params)
    processed = update_horse_results(limit)
    return f"馬の過去成績・血統を{processed}レース分収集しました(上限{limit}レース)"


def _run_backfill(params: dict) -> str:
    """WebUIからの過去データ一括取得。paramsの日付範囲はAPI側で検証済み。"""
    from datetime import date

    from src.collector import backfill  # 循環import回避のため遅延import

    start = date.fromisoformat(params["start_date"])
    end = date.fromisoformat(params["end_date"])
    return backfill.backfill(start, end)


def _scheduled_collect() -> None:
    config = load_scheduled_job_config(jobs.COLLECT)
    if config is None or not config.enabled:
        return
    if betting_priority_active():
        logger.info("発走が近いため data collection を待避します(賭け対象決定を優先)")
        return
    if not jobs.scheduled_run_due(
        jobs.COLLECT,
        config.interval_minutes,
        weekdays=config.weekdays,
        exact_time=config.exact_time,
    ):
        return
    jobs.run_scheduled(jobs.COLLECT, _run_collect)


def _scheduled_collect_horses() -> None:
    config = load_scheduled_job_config(jobs.COLLECT_HORSES)
    if config is None or not config.enabled:
        return
    if betting_priority_active():
        logger.info("発走が近いため horse results collection を待避します(賭け対象決定を優先)")
        return
    if not jobs.scheduled_run_due(
        jobs.COLLECT_HORSES,
        config.interval_minutes,
        weekdays=config.weekdays,
        exact_time=config.exact_time,
    ):
        return
    jobs.run_scheduled(jobs.COLLECT_HORSES, _run_collect_horses)


def _poll_queued_jobs() -> None:
    handlers = {
        jobs.COLLECT: _run_collect,
        jobs.BACKFILL: _run_backfill,
        jobs.COLLECT_HORSES: _run_collect_horses,
    }
    jobs.enqueue_due_reservations(list(handlers))
    jobs.process_queued(handlers)


def main() -> None:
    init_db()
    jobs.recover_stale(
        [
            jobs.COLLECT,
            jobs.BACKFILL,
            jobs.COLLECT_HORSES,
        ]
    )
    scheduler = BlockingScheduler(timezone="Asia/Tokyo")
    scheduler.add_job(_scheduled_collect, "interval", minutes=1)
    scheduler.add_job(_scheduled_collect_horses, "interval", minutes=1)
    scheduler.add_job(
        _poll_queued_jobs, "interval", seconds=jobs.POLL_INTERVAL_SECONDS
    )
    logger.info("collector started: interval=%s min", settings.COLLECT_INTERVAL_MINUTES)
    _scheduled_collect()
    _scheduled_collect_horses()
    scheduler.start()


if __name__ == "__main__":
    main()
