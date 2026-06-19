"""予測サービスのジョブ実行ロジック(予測・賭け判断・決済・学習・バックテスト)。

各 ``_run_*`` は WebUI/スケジューラから呼ばれるジョブ本体で、結果サマリ文字列を返す。
スケジュール判定・配線は scheduling.py / main.py が担う。
"""

import logging
from datetime import timedelta

from src.collector import scraper
from src.common import jobs
from src.common.config import settings
from src.common.db import session_scope
from src.common.dynamic_config import BettingConfig, load_betting_config, load_scheduled_job_config
from src.common.models import Bet, BetStatus, BettingMode, Entry, Prediction, Race, RaceOdds
from src.common.timeutils import now_jst
from src.predictor import backtest, betting, model, settlement, train
from src.predictor.features import build_features
from src.predictor.history import (
    build_entries_frame,
    load_horse_history,
    load_jockey_history,
    load_sire_map,
    load_trainer_history,
)

logger = logging.getLogger(__name__)


def _is_unfinished_race(race: Race) -> bool:
    if not race.entries:
        return False
    if any(entry.finish_position is not None for entry in race.entries):
        return False
    return race.start_time is not None and race.start_time > now_jst()


def _has_complete_odds(race: Race) -> bool:
    return bool(race.entries) and all(entry.odds is not None and entry.odds > 0 for entry in race.entries)


def _bet_decision_target_minutes(lead_minutes: int) -> int:
    return min(settings.BET_DECISION_WINDOW_MINUTES, lead_minutes)


def _refresh_race_odds(session, race: Race, day_cache: dict) -> None:
    """Refresh the target race snapshot so bet decisions use current win odds."""
    if race.race_date not in day_cache:
        day_cache[race.race_date] = scraper.fetch_upcoming_races(race.race_date)
    latest = next((item for item in day_cache[race.race_date] if item["race_key"] == race.race_key), None)
    if latest is None:
        return

    race.start_time = latest["start_time"]
    for field in ("distance", "track_type", "direction", "going", "weather", "race_class"):
        value = latest.get(field)
        if value is not None:
            setattr(race, field, value)

    entries_by_number = {entry.horse_number: entry for entry in race.entries}
    for entry_data in latest["entries"]:
        entry = entries_by_number.get(entry_data["horse_number"])
        if entry is None:
            continue
        entry.horse_name = entry_data["horse_name"]
        entry.horse_id = entry_data.get("horse_id")
        entry.sex = entry_data.get("sex")
        entry.age = entry_data.get("age")
        entry.jockey = entry_data["jockey"]
        entry.jockey_id = entry_data.get("jockey_id")
        entry.trainer = entry_data.get("trainer")
        entry.trainer_id = entry_data.get("trainer_id")
        entry.weight = entry_data["weight"]
        if entry_data.get("odds") is not None:
            entry.odds = entry_data["odds"]
            entry.pre_race_odds = entry_data["odds"]
        if entry_data.get("popularity") is not None:
            entry.popularity = entry_data["popularity"]
        if entry_data.get("horse_weight") is not None:
            entry.horse_weight = entry_data["horse_weight"]
        if entry_data.get("horse_weight_diff") is not None:
            entry.horse_weight_diff = entry_data["horse_weight_diff"]
    session.flush()


def _save_race_odds(session, race: Race, odds_by_type: dict[str, dict[str, float]]) -> None:
    existing = {
        (row.bet_type, row.combination): row
        for row in session.query(RaceOdds).filter_by(race_id=race.id).all()
    }
    for bet_type, odds_map in odds_by_type.items():
        for combination, odds in odds_map.items():
            if odds is None or odds <= 0:
                continue
            key = (bet_type, combination)
            row = existing.get(key)
            if row is None:
                session.add(
                    RaceOdds(
                        race_id=race.id,
                        bet_type=bet_type,
                        combination=combination,
                        odds=float(odds),
                    )
                )
            else:
                row.odds = float(odds)
    session.flush()


def _predict_race(
    race: Race,
    model_bundle: dict,
    session,
    history: dict,
    sire_map: dict,
    jockey_history: dict,
    trainer_history: dict,
) -> list[Prediction]:
    entries = race.entries
    entries_df = build_entries_frame(
        entries, race, history, sire_map, jockey_history, trainer_history
    )
    raw_scores, scores = model.predict_scores(model_bundle, build_features(entries_df))

    predictions = []
    for entry_id, score in scores.items():
        prediction = Prediction(
            race_id=race.id,
            entry_id=int(entry_id),
            model_version=model_bundle["version"],
            score=float(score),
            raw_score=float(raw_scores[entry_id]),
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
            bet.status = (
                BetStatus.DRY_RUN.value if settings.IPAT_DRY_RUN else BetStatus.PLACED.value
            )
        except Exception:
            bet.status = BetStatus.FAILED.value
            logger.exception("failed to place production bet: bet_id=%s", bet.id)
        session.commit()
    return len(bets)


def run_predict(params: dict) -> str:
    try:
        model_bundle = model.load_model()
    except FileNotFoundError:
        return "モデル未学習のためスキップしました(ジョブ「モデル学習」を実行してください)"

    target_races = 0
    predicted_races = 0
    skipped_existing = 0
    failed_races = 0
    with session_scope() as session:
        races = (
            session.query(Race)
            .filter(
                Race.entries.any(),
                ~Race.entries.any(Entry.finish_position.isnot(None)),
                Race.start_time.isnot(None),
                Race.start_time > now_jst(),
            )
            .all()
        )
        history = load_horse_history(session)
        sire_map = load_sire_map(session)
        jockey_history = load_jockey_history(session)
        trainer_history = load_trainer_history(session)
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
                _predict_race(
                    race,
                    model_bundle,
                    session,
                    history,
                    sire_map,
                    jockey_history,
                    trainer_history,
                )
                session.commit()
                predicted_races += 1
            except Exception:
                session.rollback()
                failed_races += 1
                logger.exception("failed to predict race_key=%s", race.race_key)

    summary = (
        f"対象未確定レース={target_races}件, "
        f"新規予測={predicted_races}件, "
        f"既存予測あり={skipped_existing}件"
    )
    if failed_races:
        summary += f", 失敗レース={failed_races}件"
    return summary


def run_bet_decide(params: dict) -> str:
    config = load_betting_config()
    now = now_jst()

    target_races = 0
    skipped_no_predictions = 0
    skipped_no_complete_odds = 0
    skipped_already_bet = 0
    new_bets = 0
    failed_races = 0
    schedule_config = load_scheduled_job_config(jobs.BET_DECIDE)
    lead_minutes = (
        schedule_config.before_start_minutes
        if schedule_config is not None and schedule_config.before_start_minutes is not None
        else settings.BET_DECISION_LEAD_MINUTES
    )
    target_minutes = _bet_decision_target_minutes(lead_minutes)
    day_cache: dict = {}

    with session_scope() as session:
        races = (
            session.query(Race)
            .filter(
                Race.start_time.isnot(None),
                Race.start_time > now,
                Race.start_time
                <= now + timedelta(minutes=target_minutes),
                Race.entries.any(),
                ~Race.entries.any(Entry.finish_position.isnot(None)),
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

                _refresh_race_odds(session, race, day_cache)

                if not _has_complete_odds(race):
                    skipped_no_complete_odds += 1
                    continue

                already_bet = (
                    session.query(Bet).filter_by(race_id=race.id, mode=config.mode).first()
                )
                if already_bet is not None:
                    skipped_already_bet += 1
                    continue

                odds_by_type = scraper.fetch_supported_odds(race.race_key)
                _save_race_odds(session, race, odds_by_type)
                bets = betting.decide_bets(race, predictions, config, odds_by_type=odds_by_type)
                if bets:
                    new_bets += _place_bets(session, bets, config)
            except Exception:
                session.rollback()
                failed_races += 1
                logger.exception("failed to decide bets race_key=%s", race.race_key)

    summary = (
        f"mode={config.mode}, "
        f"対象レース(発走{target_minutes}分以内・最新オッズ確認対象)="
        f"{target_races}件, "
        f"新規賭け={new_bets}件, "
        f"予測なし={skipped_no_predictions}件, "
        f"オッズ未入力={skipped_no_complete_odds}件, "
        f"既存賭けあり={skipped_already_bet}件"
    )
    if failed_races:
        summary += f", 失敗レース={failed_races}件"
    return summary


def run_settle(params: dict) -> str:
    settled = settlement.settle_pending_races()
    return f"決済={settled}件"


def run_train(params: dict) -> str:
    return train.train_model()


def run_backtest(params: dict) -> str:
    """回収率バックテスト。paramsの日付範囲はAPI側で検証済み。"""
    from datetime import date

    start = date.fromisoformat(params["start_date"])
    end = date.fromisoformat(params["end_date"])
    return backtest.format_summary(backtest.run_backtest(start, end))
