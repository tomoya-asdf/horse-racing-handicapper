"""予測サービスのスケジューラ兼ジョブ配線。

業務ロジックは責務ごとのモジュールに分離している:

- tasks      : ジョブ実行本体(run_predict / run_bet_decide / run_settle /
               run_train / run_backtest)と補助関数
- scheduling : スケジュール判定(_scheduled_*)と発走時刻からの due 逆算

本モジュールはそれらを組み立て、APScheduler で定期実行・キュー処理するだけに留める。
"""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from src.common import jobs
from src.common.db import init_db
from src.predictor.scheduling import (
    scheduled_bet_decide,
    scheduled_predict,
    scheduled_settle,
    scheduled_train,
)
from src.predictor.tasks import (
    run_backtest,
    run_bet_decide,
    run_predict,
    run_settle,
    run_train,
)

logging.basicConfig(level=logging.INFO)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def _poll_queued_jobs() -> None:
    handlers = {
        jobs.PREDICT: run_predict,
        jobs.BET_DECIDE: run_bet_decide,
        jobs.SETTLE: run_settle,
        jobs.TRAIN: run_train,
        jobs.BACKTEST: run_backtest,
    }
    jobs.enqueue_due_reservations(list(handlers))
    jobs.process_queued(handlers)


def main() -> None:
    init_db()
    jobs.recover_stale([jobs.PREDICT, jobs.BET_DECIDE, jobs.SETTLE, jobs.TRAIN, jobs.BACKTEST])
    scheduler = BlockingScheduler(timezone="Asia/Tokyo")
    scheduler.add_job(scheduled_predict, "interval", minutes=1)
    scheduler.add_job(scheduled_bet_decide, "interval", minutes=1)
    scheduler.add_job(scheduled_settle, "interval", minutes=1)
    scheduler.add_job(scheduled_train, "interval", minutes=1)
    scheduler.add_job(_poll_queued_jobs, "interval", seconds=jobs.POLL_INTERVAL_SECONDS)
    logger.info("predictor started")
    scheduled_predict()
    scheduled_bet_decide()
    scheduled_train()
    scheduler.start()


if __name__ == "__main__":
    main()
