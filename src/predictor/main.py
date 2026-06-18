"""予測サービスのスケジューラ兼ジョブ配線。

業務ロジックは責務ごとのモジュールに分離している:

- tasks      : ジョブ実行本体(_run_predict / _run_bet_decide / _run_settle /
               _run_train / _run_backtest)と補助関数
- scheduling : スケジュール判定(_scheduled_*)と発走時刻からの due 逆算

本モジュールはそれらを組み立て、APScheduler で定期実行・キュー処理するだけに留める。
"""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from src.common import jobs
from src.common.db import init_db
from src.predictor.scheduling import (
    _scheduled_bet_decide,
    _scheduled_predict,
    _scheduled_settle,
    _scheduled_train,
)
from src.predictor.tasks import (
    _run_backtest,
    _run_bet_decide,
    _run_predict,
    _run_settle,
    _run_train,
)

logging.basicConfig(level=logging.INFO)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def _poll_queued_jobs() -> None:
    handlers = {
        jobs.PREDICT: _run_predict,
        jobs.BET_DECIDE: _run_bet_decide,
        jobs.SETTLE: _run_settle,
        jobs.TRAIN: _run_train,
        jobs.BACKTEST: _run_backtest,
    }
    jobs.enqueue_due_reservations(list(handlers))
    jobs.process_queued(handlers)


def main() -> None:
    init_db()
    jobs.recover_stale([jobs.PREDICT, jobs.BET_DECIDE, jobs.SETTLE, jobs.TRAIN, jobs.BACKTEST])
    scheduler = BlockingScheduler(timezone="Asia/Tokyo")
    scheduler.add_job(_scheduled_predict, "interval", minutes=1)
    scheduler.add_job(_scheduled_bet_decide, "interval", minutes=1)
    scheduler.add_job(_scheduled_settle, "interval", minutes=1)
    scheduler.add_job(_scheduled_train, "interval", minutes=1)
    scheduler.add_job(_poll_queued_jobs, "interval", seconds=jobs.POLL_INTERVAL_SECONDS)
    logger.info("predictor started")
    _scheduled_predict()
    _scheduled_bet_decide()
    _scheduled_train()
    scheduler.start()


if __name__ == "__main__":
    main()
