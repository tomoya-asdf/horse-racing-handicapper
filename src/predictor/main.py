import logging
from datetime import timedelta

import pandas as pd
from apscheduler.schedulers.blocking import BlockingScheduler

from src.common import jobs
from src.common.config import settings
from src.common.db import get_session, init_db
from src.common.dynamic_config import BettingConfig, load_betting_config
from src.common.models import Bet, BetStatus, BettingMode, Entry, Prediction, Race
from src.common.timeutils import now_jst
from src.predictor import betting, model, settlement, train
from src.predictor.features import build_features

logging.basicConfig(level=logging.INFO)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def _is_unfinished_race(race: Race) -> bool:
    return bool(race.entries) and any(entry.finish_position is None for entry in race.entries)


def _has_complete_odds(race: Race) -> bool:
    return bool(race.entries) and all(entry.odds is not None and entry.odds > 0 for entry in race.entries)


def _predict_race(race: Race, model_bundle: dict, session) -> list[Prediction]:
    entries = race.entries
    entries_df = pd.DataFrame(
        {
            "horse_number": [entry.horse_number for entry in entries],
            "weight": [entry.weight for entry in entries],
            "jockey": [entry.jockey for entry in entries],
        },
        index=[entry.id for entry in entries],
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


def _latest_predictions(session, race_id: int) -> list[Prediction]:
    latest = (
        session.query(Prediction.model_version)
        .filter(Prediction.race_id == race_id)
        .order_by(Prediction.created_at.desc(), Prediction.id.desc())
        .first()
    )
    if latest is None:
        return []
    return (
        session.query(Prediction)
        .filter_by(race_id=race_id, model_version=latest.model_version)
        .all()
    )


def _place_bets(session, bets: list[Bet], config: BettingConfig) -> int:
    is_prod = config.mode == BettingMode.PROD.value
    for bet in bets:
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
            bet.status = BetStatus.FAILED.value
            logger.exception("failed to place production bet: bet_id=%s", bet.id)
        session.commit()
    return len(bets)


def _run_predict(params: dict) -> str:
    try:
        model_bundle = model.load_model()
    except FileNotFoundError:
        return "モデル未学習のためスキップしました(ジョブ「モデル学習」を実行してください)"

    target_races = 0
    predicted_races = 0
    skipped_existing = 0
    failed_races = 0
    session = get_session()
    try:
        races = (
            session.query(Race)
            .filter(Race.entries.any(Entry.finish_position.is_(None)))
            .all()
        )
        for race in races:
            if not _is_unfinished_race(race):
                continue
            target_races += 1
            try:
                existing = (
                    session.query(Prediction.id)
                    .filter_by(race_id=race.id, model_version=model_bundle["version"])
                    .first()
                )
                if existing is not None:
                    skipped_existing += 1
                    continue
                _predict_race(race, model_bundle, session)
                session.commit()
                predicted_races += 1
            except Exception:
                session.rollback()
                failed_races += 1
                logger.exception("failed to predict race_key=%s", race.race_key)
    finally:
        session.close()

    summary = (
        f"対象未確定レース={target_races}件, "
        f"新規予測={predicted_races}件, "
        f"既存予測あり={skipped_existing}件"
    )
    if failed_races:
        summary += f", 失敗レース={failed_races}件"
    return summary


def _run_bet_decide(params: dict) -> str:
    config = load_betting_config()
    now = now_jst()

    target_races = 0
    skipped_no_predictions = 0
    skipped_no_complete_odds = 0
    skipped_already_bet = 0
    new_bets = 0
    failed_races = 0

    session = get_session()
    try:
        races = (
            session.query(Race)
            .filter(
                Race.start_time.isnot(None),
                Race.start_time > now,
                Race.start_time
                <= now + timedelta(minutes=settings.BET_DECISION_WINDOW_MINUTES),
                Race.entries.any(Entry.finish_position.is_(None)),
            )
            .all()
        )
        for race in races:
            if not _is_unfinished_race(race):
                continue
            target_races += 1
            try:
                predictions = _latest_predictions(session, race.id)
                if not predictions:
                    skipped_no_predictions += 1
                    continue

                if not _has_complete_odds(race):
                    skipped_no_complete_odds += 1
                    continue

                already_bet = (
                    session.query(Bet).filter_by(race_id=race.id, mode=config.mode).first()
                )
                if already_bet is not None:
                    skipped_already_bet += 1
                    continue

                bets = betting.decide_bets(race, predictions, config)
                if bets:
                    new_bets += _place_bets(session, bets, config)
            except Exception:
                session.rollback()
                failed_races += 1
                logger.exception("failed to decide bets race_key=%s", race.race_key)
    finally:
        session.close()

    summary = (
        f"mode={config.mode}, "
        f"対象レース(発走{settings.BET_DECISION_WINDOW_MINUTES}分以内・未確定・オッズ確認対象)="
        f"{target_races}件, "
        f"新規賭け={new_bets}件, "
        f"予測なし={skipped_no_predictions}件, "
        f"オッズ未入力={skipped_no_complete_odds}件, "
        f"既存賭けあり={skipped_already_bet}件"
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


def _scheduled_bet_decide() -> None:
    jobs.run_scheduled(jobs.BET_DECIDE, _run_bet_decide)


def _scheduled_settle() -> None:
    jobs.run_scheduled(jobs.SETTLE, _run_settle)


def _poll_queued_jobs() -> None:
    jobs.process_queued(
        {
            jobs.PREDICT: _run_predict,
            jobs.BET_DECIDE: _run_bet_decide,
            jobs.SETTLE: _run_settle,
            jobs.TRAIN: _run_train,
        }
    )


def main() -> None:
    init_db()
    jobs.recover_stale([jobs.PREDICT, jobs.BET_DECIDE, jobs.SETTLE, jobs.TRAIN])
    scheduler = BlockingScheduler(timezone="Asia/Tokyo")
    scheduler.add_job(_scheduled_predict, "interval", minutes=settings.PREDICT_INTERVAL_MINUTES)
    scheduler.add_job(_scheduled_bet_decide, "interval", minutes=settings.PREDICT_INTERVAL_MINUTES)
    scheduler.add_job(_scheduled_settle, "interval", minutes=settings.PREDICT_INTERVAL_MINUTES)
    scheduler.add_job(_poll_queued_jobs, "interval", seconds=jobs.POLL_INTERVAL_SECONDS)
    logger.info("predictor started")
    _scheduled_predict()
    _scheduled_bet_decide()
    scheduler.start()


if __name__ == "__main__":
    main()
