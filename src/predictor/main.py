import logging
from datetime import timedelta

import pandas as pd
from apscheduler.schedulers.blocking import BlockingScheduler

from src.common import jobs
from src.common.config import settings
from src.common.db import get_session, init_db
from src.common.dynamic_config import BettingConfig, load_betting_config
from src.common.models import Bet, BetStatus, BettingMode, Prediction, Race
from src.common.timeutils import now_jst
from src.predictor import betting, model, settlement, train
from src.predictor.features import build_features

logging.basicConfig(level=logging.INFO)
# 5秒間隔のジョブポーリングがINFOログを埋め尽くすため、APSchedulerのログは抑制する
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def _predict_race(race: Race, model_bundle: dict, session) -> list[Prediction]:
    entries = race.entries
    entries_df = pd.DataFrame(
        {
            "horse_number": [e.horse_number for e in entries],
            "weight": [e.weight for e in entries],
            "odds": [e.odds for e in entries],
        },
        index=[e.id for e in entries],
    )
    scores = model.predict(model_bundle, build_features(entries_df))

    predictions = []
    for entry_id, score in scores.items():
        prediction = Prediction(
            race_id=race.id,
            entry_id=int(entry_id),
            model_version=model_bundle["version"],
            score=float(score),
        )
        session.add(prediction)
        predictions.append(prediction)
    return predictions


def _process_race(session, race: Race, model_bundle: dict, config: BettingConfig) -> int:
    """1レース分の予測・賭けを行い、新規に成立を試みた賭けの数を返す。"""
    if not race.entries:
        return 0

    predictions = (
        session.query(Prediction)
        .filter_by(race_id=race.id, model_version=model_bundle["version"])
        .all()
    )
    if not predictions:
        predictions = _predict_race(race, model_bundle, session)

    already_bet = (
        session.query(Bet).filter_by(race_id=race.id, mode=config.mode).first()
    )
    if already_bet is not None:
        session.commit()  # 予測のみ保存
        return 0

    is_prod = config.mode == BettingMode.PROD.value
    bets = betting.decide_bets(race, predictions, config)
    for bet in bets:
        # prodでは購入操作の「前」に必ずpendingとしてコミットする。購入の途中で
        # プロセスが落ちても記録が残り、次回ジョブはalready_bet判定により
        # 同一レースへの重複購入を行わない(フェイルクローズ)
        bet.status = BetStatus.PENDING.value if is_prod else BetStatus.PLACED.value
        session.add(bet)
    session.commit()

    if not is_prod:
        return len(bets)

    for bet in bets:
        try:
            betting.place_bet_production(bet)
            bet.status = BetStatus.PLACED.value
        except Exception:
            # 失敗した賭けはfailedとして残す(お金は動いていない)。
            # 決済・回収率の集計からは除外される
            bet.status = BetStatus.FAILED.value
            logger.exception("failed to place production bet: bet_id=%s", bet.id)
        session.commit()
    return len(bets)


def _run_predict(params: dict) -> str:
    config = load_betting_config()
    try:
        model_bundle = model.load_model()
    except FileNotFoundError:
        return "モデル未学習のためスキップしました(ジョブ「モデル学習」を実行してください)"

    new_bets = 0
    failed_races = 0
    session = get_session()
    try:
        # レースは数日先の分まで収集されるが、賭け判断は発走が近いレースに限定する
        # (収集時点の古いオッズに基づく予測・賭けを避けるため)
        now = now_jst()
        races = (
            session.query(Race)
            .filter(
                Race.start_time.isnot(None),
                Race.start_time > now,
                Race.start_time <= now + timedelta(minutes=settings.BET_WINDOW_MINUTES),
            )
            .all()
        )
        for race in races:
            try:
                new_bets += _process_race(session, race, model_bundle, config)
            except Exception:
                session.rollback()
                failed_races += 1
                logger.exception("failed to process race_key=%s", race.race_key)
    finally:
        session.close()

    summary = (
        f"mode={config.mode}, "
        f"対象レース(発走{settings.BET_WINDOW_MINUTES}分以内)={len(races)}件, "
        f"新規賭け={new_bets}件"
    )
    if failed_races:
        summary += f", 失敗レース={failed_races}件"
    return summary


def _run_settle(params: dict) -> str:
    settled = settlement.settle_pending_races()
    return f"決済={settled}件"


def _run_train(params: dict) -> str:
    return train.train_model()


def _scheduled_predict() -> None:
    jobs.run_scheduled(jobs.PREDICT, _run_predict)


def _scheduled_settle() -> None:
    jobs.run_scheduled(jobs.SETTLE, _run_settle)


def _poll_queued_jobs() -> None:
    jobs.process_queued(
        {
            jobs.PREDICT: _run_predict,
            jobs.SETTLE: _run_settle,
            jobs.TRAIN: _run_train,
        }
    )


def main() -> None:
    init_db()
    jobs.recover_stale([jobs.PREDICT, jobs.SETTLE, jobs.TRAIN])
    scheduler = BlockingScheduler(timezone="Asia/Tokyo")
    scheduler.add_job(_scheduled_predict, "interval", minutes=settings.PREDICT_INTERVAL_MINUTES)
    scheduler.add_job(_scheduled_settle, "interval", minutes=settings.PREDICT_INTERVAL_MINUTES)
    scheduler.add_job(_poll_queued_jobs, "interval", seconds=jobs.POLL_INTERVAL_SECONDS)
    logger.info("predictor started")
    _scheduled_predict()
    scheduler.start()


if __name__ == "__main__":
    main()
