"""WebUI用のバックエンドAPI(FastAPI)。

システム全体の状況・履歴の参照、ジョブの手動実行、設定変更を提供する。
ジョブの実行自体は行わず、job_runs テーブルへの登録のみ行い、
担当サービス(collector/predictor)がポーリングして実行する。
ビルド済みのフロントエンド(webui/dist)も同じプロセスから配信する。
"""

import logging
import secrets
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from src.common import jobs
from src.common.config import settings
from src.common.db import get_session, init_db
from src.common.dynamic_config import (
    get_settings_view,
    load_betting_config,
    save_settings,
    scheduled_jobs_view,
)
from src.common.models import (
    Bet,
    BetStatus,
    BettingMode,
    Entry,
    Horse,
    HorseResult,
    Jockey,
    JockeyResult,
    JobRun,
    Prediction,
    Race,
    Trainer,
    TrainerResult,
)
from src.common.paths import MODEL_PATH
from src.common.timeutils import now_jst

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="競馬予測AI 管理API")

JOB_LABELS = {
    jobs.COLLECT: "データ収集",
    jobs.BACKFILL: "過去データ取得",
    jobs.COLLECT_HORSES: "馬過去成績収集",
    jobs.PREDICT: "AI予想",
    jobs.BET_DECIDE: "賭け対象決定",
    jobs.SETTLE: "決済",
    jobs.TRAIN: "モデル学習",
    jobs.BACKTEST: "回収率バックテスト",
}

# 一度にバックフィルできる最大日数(netkeibaへの負荷を抑えるため)
JOB_LABELS.update(
    {
        jobs.COLLECT_JOCKEYS: "騎手過去戦績収集",
        jobs.COLLECT_TRAINERS: "調教師過去戦績収集",
    }
)

BACKFILL_MAX_DAYS = 31
ADMIN_COOKIE_NAME = "admin_session"
ADMIN_SESSION_SECONDS = 60 * 60 * 12
ADMIN_SESSIONS: set[str] = set()


@app.on_event("startup")
def startup() -> None:
    init_db()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _bet_stats(bets: list[Bet]) -> dict:
    placed = [b for b in bets if b.status == BetStatus.PLACED.value]
    settled = [b for b in placed if b.is_settled]
    invested = sum(b.amount for b in settled)
    payout = sum(b.payout or 0 for b in settled)
    return {
        "invested": invested,
        "payout": payout,
        "recovery_rate": (payout / invested * 100) if invested else None,
        "settled_count": len(settled),
        "unsettled_count": len(placed) - len(settled),
        "pending_count": sum(1 for b in bets if b.status == BetStatus.PENDING.value),
        "dry_run_count": sum(1 for b in bets if b.status == BetStatus.DRY_RUN.value),
        "failed_count": sum(1 for b in bets if b.status == BetStatus.FAILED.value),
    }


def _job_to_dict(run: JobRun) -> dict:
    return {
        "id": run.id,
        "job_name": run.job_name,
        "label": JOB_LABELS.get(run.job_name, run.job_name),
        "trigger": run.trigger,
        "status": run.status,
        "detail": run.detail,
        "created_at": _iso(run.created_at),
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
    }


def _latest_prediction_model_version(session) -> str | None:
    row = (
        session.query(Prediction.model_version)
        .filter(Prediction.model_version.isnot(None))
        .order_by(Prediction.created_at.desc(), Prediction.id.desc())
        .first()
    )
    return row[0] if row else None


def _model_info(session=None) -> dict:
    if not MODEL_PATH.exists():
        version = _latest_prediction_model_version(session) if session is not None else None
        return {"trained": bool(version), "version": version, "trained_at": None}
    info = {
        "trained": True,
        "version": _latest_prediction_model_version(session) if session is not None else None,
        "trained_at": None,
    }
    try:
        info["trained_at"] = datetime.fromtimestamp(MODEL_PATH.stat().st_mtime).isoformat()
    except OSError:
        pass
    if info["version"] is None:
        try:
            import joblib

            bundle = joblib.load(MODEL_PATH)
            info["version"] = bundle.get("version")
        except Exception as exc:
            logger.warning("failed to read model bundle metadata: %s", exc)
    return info


def _rank_entries(values: dict[int, float], reverse: bool = False) -> dict[int, int]:
    ranked: dict[int, int] = {}
    previous_value = None
    previous_rank = 0
    for index, (entry_id, value) in enumerate(
        sorted(values.items(), key=lambda item: item[1], reverse=reverse),
        start=1,
    ):
        if previous_value is None or value != previous_value:
            previous_rank = index
            previous_value = value
        ranked[entry_id] = previous_rank
    return ranked


def _admin_configured() -> bool:
    return bool(settings.ADMIN_LOGIN_ID and settings.ADMIN_PASSWORD)


def _is_admin_request(request: Request) -> bool:
    token = request.cookies.get(ADMIN_COOKIE_NAME)
    return bool(token and token in ADMIN_SESSIONS)


def require_admin(request: Request) -> None:
    if not _admin_configured():
        raise HTTPException(status_code=503, detail="管理者ログインが設定されていません")
    if not _is_admin_request(request):
        raise HTTPException(status_code=401, detail="管理者ログインが必要です")


@app.get("/api/auth/status")
def auth_status(request: Request) -> dict:
    return {"configured": _admin_configured(), "authenticated": _is_admin_request(request)}


@app.post("/api/auth/login")
def auth_login(values: dict, response: Response) -> dict:
    if not _admin_configured():
        raise HTTPException(status_code=503, detail="ADMIN_LOGIN_ID / ADMIN_PASSWORD を.envに設定してください")
    login_id = str(values.get("login_id", ""))
    password = str(values.get("password", ""))
    if not (
        secrets.compare_digest(login_id, settings.ADMIN_LOGIN_ID)
        and secrets.compare_digest(password, settings.ADMIN_PASSWORD)
    ):
        raise HTTPException(status_code=401, detail="ログインIDまたはパスワードが違います")
    token = secrets.token_urlsafe(32)
    ADMIN_SESSIONS.add(token)
    response.set_cookie(
        ADMIN_COOKIE_NAME,
        token,
        max_age=ADMIN_SESSION_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return {"authenticated": True}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(ADMIN_COOKIE_NAME)
    if token:
        ADMIN_SESSIONS.discard(token)
    response.delete_cookie(ADMIN_COOKIE_NAME)
    return {"authenticated": False}


@app.get("/api/overview")
def overview(request: Request) -> dict:
    is_admin = _is_admin_request(request)
    session = get_session()
    try:
        race_count = session.query(func.count(Race.id)).scalar() or 0
        finished_race_count = (
            session.query(func.count(func.distinct(Entry.race_id)))
            .filter(Entry.finish_position.isnot(None))
            .scalar()
            or 0
        )
        horse_result_horse_count = (
            session.query(func.count(func.distinct(HorseResult.horse_id))).scalar() or 0
        )
        jockey_result_jockey_count = (
            session.query(func.count(func.distinct(JockeyResult.jockey_id))).scalar() or 0
        )
        trainer_result_trainer_count = (
            session.query(func.count(func.distinct(TrainerResult.trainer_id))).scalar() or 0
        )
        last_collected_at = session.query(func.max(Race.created_at)).scalar()
        upcoming_race_count = (
            session.query(func.count(Race.id))
            .filter(Race.start_time.isnot(None), Race.start_time > now_jst())
            .scalar()
            or 0
        )

        modes = {}
        visible_modes = [BettingMode.SIM.value]
        if is_admin:
            visible_modes.append(BettingMode.PROD.value)
        for mode in visible_modes:
            bets = session.query(Bet).filter(Bet.mode == mode).all()
            modes[mode] = _bet_stats(bets)

        latest_jobs = []
        for job_name in jobs.ALL_JOBS:
            run = (
                session.query(JobRun)
                .filter(JobRun.job_name == job_name)
                .order_by(JobRun.created_at.desc())
                .first()
            )
            if run is not None:
                latest_jobs.append(_job_to_dict(run))
        model_info = _model_info(session)
    finally:
        session.close()

    return {
        "model": model_info,
        "data": {
            "race_count": race_count,
            "finished_race_count": finished_race_count,
            "horse_result_horse_count": horse_result_horse_count,
            "jockey_result_jockey_count": jockey_result_jockey_count,
            "trainer_result_trainer_count": trainer_result_trainer_count,
            "upcoming_race_count": upcoming_race_count,
            "last_collected_at": _iso(last_collected_at),
        },
        "modes": modes,
        "latest_jobs": latest_jobs,
        "settings": get_settings_view(include_env=False),
    }


@app.get("/api/races")
def list_races(
    request: Request,
    limit: int = 30,
    offset: int = 0,
    race_name: str | None = None,
    race_date: str | None = None,
    race_number: int | None = None,
    venue: str | None = None,
    status: str | None = None,
    horse_name: str | None = None,
    jockey: str | None = None,
    prediction: str | None = None,
    bet: str | None = None,
) -> dict:
    is_admin = _is_admin_request(request)
    page_limit = min(max(limit, 1), 200)
    page_offset = max(offset, 0)
    session = get_session()
    try:
        venues = [
            row[0]
            for row in session.query(Race.venue)
            .filter(Race.venue.isnot(None))
            .distinct()
            .order_by(Race.venue)
            .all()
        ]
        query = session.query(Race)
        if race_name:
            query = query.filter(Race.race_name.ilike(f"%{race_name.strip()}%"))
        if race_date:
            try:
                parsed_date = datetime.strptime(race_date, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(status_code=400, detail="race_date は YYYY-MM-DD で指定してください")
            query = query.filter(Race.race_date == parsed_date)
        if race_number is not None:
            if race_number < 1 or race_number > 12:
                raise HTTPException(status_code=400, detail="race_number は 1-12 で指定してください")
            query = query.filter(Race.race_number == race_number)
        if venue:
            query = query.filter(Race.venue == venue.strip())
        if status == "finished":
            query = query.filter(Race.entries.any(Entry.finish_position.isnot(None)))
        elif status == "unfinished":
            query = query.filter(
                Race.start_time.isnot(None),
                Race.start_time > now_jst(),
                ~Race.entries.any(Entry.finish_position.isnot(None)),
            )
        elif status == "upcoming":
            query = query.filter(Race.start_time.isnot(None), Race.start_time > now_jst())
        if horse_name:
            query = query.filter(Race.entries.any(Entry.horse_name.ilike(f"%{horse_name.strip()}%")))
        if jockey:
            query = query.filter(Race.entries.any(Entry.jockey.ilike(f"%{jockey.strip()}%")))
        if prediction == "yes":
            query = query.filter(Race.predictions.any())
        elif prediction == "no":
            query = query.filter(~Race.predictions.any())
        if bet == "yes":
            query = query.filter(
                Race.bets.any()
                if is_admin
                else Race.bets.any(Bet.mode == BettingMode.SIM.value)
            )
        elif bet == "no":
            query = query.filter(
                ~Race.bets.any()
                if is_admin
                else ~Race.bets.any(Bet.mode == BettingMode.SIM.value)
            )

        query = query.distinct()
        total = query.with_entities(func.count(func.distinct(Race.id))).scalar() or 0
        races = (
            query.options(
                selectinload(Race.entries),
                selectinload(Race.predictions),
                selectinload(Race.bets),
            )
            .order_by(Race.race_date.desc(), Race.start_time.desc(), Race.id.desc())
            .offset(page_offset)
            .limit(page_limit)
            .all()
        )

        items = []
        for race in races:
            entry_map = {e.id: e for e in race.entries}
            top = None
            if race.predictions:
                latest = max(race.predictions, key=lambda p: p.created_at or datetime.min)
                candidates = [
                    p for p in race.predictions if p.model_version == latest.model_version
                ]
                best = max(candidates, key=lambda p: p.score)
                best_entry = entry_map.get(best.entry_id)
                top = {
                    "horse_number": best_entry.horse_number if best_entry else None,
                    "horse_id": best_entry.horse_id if best_entry else None,
                    "horse_name": best_entry.horse_name if best_entry else None,
                    "score": best.score,
                    "model_version": best.model_version,
                }
            items.append(
                {
                    "id": race.id,
                    "race_key": race.race_key,
                    "race_date": _iso(race.race_date),
                    "venue": race.venue,
                    "race_number": race.race_number,
                    "race_name": race.race_name,
                    "start_time": _iso(race.start_time),
                    "distance": race.distance,
                    "track_type": race.track_type,
                    "going": race.going,
                    "race_class": race.race_class,
                    "entry_count": len(race.entries),
                    "finished": any(e.finish_position is not None for e in race.entries),
                    "top_prediction": top,
                    "bet_count": len(
                        [b for b in race.bets if is_admin or b.mode != BettingMode.PROD.value]
                    ),
                }
            )
    finally:
        session.close()
    return {
        "races": items,
        "total": total,
        "limit": page_limit,
        "offset": page_offset,
        "venues": venues,
    }


@app.get("/api/races/{race_id}")
def race_detail(request: Request, race_id: int) -> dict:
    is_admin = _is_admin_request(request)
    session = get_session()
    try:
        race = (
            session.query(Race)
            .options(
                selectinload(Race.entries),
                selectinload(Race.predictions),
                selectinload(Race.bets),
            )
            .filter(Race.id == race_id)
            .one_or_none()
        )
        if race is None:
            raise HTTPException(status_code=404, detail="race not found")

        score_map: dict[int, float] = {}
        model_version = None
        if race.predictions:
            latest = max(race.predictions, key=lambda p: p.created_at or datetime.min)
            model_version = latest.model_version
            score_map = {
                p.entry_id: p.score
                for p in race.predictions
                if p.model_version == model_version
            }
        visible_bets = [b for b in race.bets if is_admin or b.mode != BettingMode.PROD.value]
        bet_entry_ids = {b.entry_id for b in visible_bets}
        betting_config = load_betting_config()
        ai_rank_map = _rank_entries(score_map, reverse=True)
        odds_rank_map = _rank_entries(
            {e.id: e.odds for e in race.entries if e.odds is not None and e.odds > 0}
        )

        ranked_entries = sorted(
            [e for e in race.entries if e.id in score_map],
            key=lambda e: score_map[e.id],
            reverse=True,
        )
        score_gap = None
        race_shape = None
        if len(ranked_entries) >= 2:
            score_gap = score_map[ranked_entries[0].id] - score_map[ranked_entries[1].id]
            if score_gap >= 0.05:
                race_shape = "本命寄り"
            elif score_gap <= 0.02:
                race_shape = "混戦"
            else:
                race_shape = "やや混戦"

        top_ai = [
            {
                "entry_id": e.id,
                "horse_number": e.horse_number,
                "horse_id": e.horse_id,
                "horse_name": e.horse_name,
                "score": score_map[e.id],
                "ai_rank": ai_rank_map.get(e.id),
                "odds": e.odds,
                "odds_rank": odds_rank_map.get(e.id),
                "expected_value": (
                    score_map[e.id] * e.odds if e.odds is not None and e.odds > 0 else None
                ),
            }
            for e in ranked_entries[:3]
        ]

        entries = [
            {
                "id": e.id,
                "horse_number": e.horse_number,
                "horse_id": e.horse_id,
                "horse_name": e.horse_name,
                "sex": e.sex,
                "age": e.age,
                "jockey": e.jockey,
                "jockey_id": e.jockey_id,
                "trainer": e.trainer,
                "trainer_id": e.trainer_id,
                "weight": e.weight,
                "horse_weight": e.horse_weight,
                "horse_weight_diff": e.horse_weight_diff,
                "odds": e.odds,
                "popularity": e.popularity if e.popularity is not None else odds_rank_map.get(e.id),
                "finish_position": e.finish_position,
                "score": score_map.get(e.id),
                "ai_rank": ai_rank_map.get(e.id),
                "odds_rank": odds_rank_map.get(e.id),
                "expected_value": (
                    score_map[e.id] * e.odds
                    if e.id in score_map and e.odds is not None and e.odds > 0
                    else None
                ),
                "value_label": (
                    "妙味あり"
                    if e.id in score_map
                    and e.odds is not None
                    and e.odds > 0
                    and score_map[e.id] * e.odds >= betting_config.min_expected_value
                    else "見送り"
                    if e.id in score_map and e.odds is not None and e.odds > 0
                    else None
                ),
                "ai_vs_odds": (
                    "AI評価高め"
                    if e.id in ai_rank_map
                    and e.id in odds_rank_map
                    and ai_rank_map[e.id] < odds_rank_map[e.id]
                    else None
                ),
                "has_bet": e.id in bet_entry_ids,
            }
            for e in sorted(race.entries, key=lambda e: e.horse_number)
        ]
        bets = [
            {
                "id": b.id,
                "mode": b.mode,
                "status": b.status,
                "bet_type": b.bet_type,
                "horse_number": b.entry.horse_number if b.entry else None,
                "combination": b.combination,
                "amount": b.amount,
                "odds_at_bet": b.odds_at_bet,
                "payout": b.payout,
                "is_settled": b.is_settled,
                "placed_at": _iso(b.placed_at),
            }
            for b in visible_bets
        ]
        return {
            "id": race.id,
            "race_key": race.race_key,
            "race_date": _iso(race.race_date),
            "venue": race.venue,
            "race_number": race.race_number,
            "race_name": race.race_name,
            "start_time": _iso(race.start_time),
            "distance": race.distance,
            "track_type": race.track_type,
            "direction": race.direction,
            "going": race.going,
            "weather": race.weather,
            "race_class": race.race_class,
            "model_version": model_version,
            "analysis": {
                "top_ai": top_ai,
                "score_gap": score_gap,
                "race_shape": race_shape,
            },
            "entries": entries,
            "bets": bets,
        }
    finally:
        session.close()


@app.get("/api/horses/{horse_id}")
def horse_detail(horse_id: str) -> dict:
    session = get_session()
    try:
        horse = session.get(Horse, horse_id)
        results = (
            session.query(HorseResult)
            .filter(HorseResult.horse_id == horse_id)
            .order_by(HorseResult.race_date.desc().nullslast(), HorseResult.id.desc())
            .limit(30)
            .all()
        )
        if horse is None and not results:
            entry = session.query(Entry).filter(Entry.horse_id == horse_id).first()
            if entry is None:
                raise HTTPException(status_code=404, detail="horse not found")
            name = entry.horse_name
            sire_id = None
            sire_name = None
            results_fetched_at = None
        else:
            name = horse.name if horse else None
            sire_id = horse.sire_id if horse else None
            sire_name = horse.sire_name if horse else None
            results_fetched_at = _iso(horse.results_fetched_at) if horse else None

        return {
            "horse_id": horse_id,
            "name": name,
            "sire_id": sire_id,
            "sire_name": sire_name,
            "results_fetched_at": results_fetched_at,
            "results": [
                {
                    "race_key": r.race_key,
                    "race_date": r.race_date.isoformat() if r.race_date else None,
                    "venue": r.venue,
                    "race_name": r.race_name,
                    "field_size": r.field_size,
                    "horse_number": r.horse_number,
                    "odds": r.odds,
                    "popularity": r.popularity,
                    "finish_position": r.finish_position,
                    "jockey": r.jockey,
                    "weight": r.weight,
                    "distance": r.distance,
                    "track_type": r.track_type,
                    "going": r.going,
                    "time_seconds": r.time_seconds,
                    "last_3f": r.last_3f,
                    "horse_weight": r.horse_weight,
                }
                for r in results
            ],
        }
    finally:
        session.close()


@app.get("/api/jockeys/{jockey_id}")
def jockey_detail(jockey_id: str) -> dict:
    session = get_session()
    try:
        jockey = session.get(Jockey, jockey_id)
        results = (
            session.query(JockeyResult)
            .filter(JockeyResult.jockey_id == jockey_id)
            .order_by(JockeyResult.race_date.desc().nullslast(), JockeyResult.id.desc())
            .limit(50)
            .all()
        )
        if jockey is None and not results:
            entry = session.query(Entry).filter(Entry.jockey_id == jockey_id).first()
            if entry is None:
                raise HTTPException(status_code=404, detail="jockey not found")
            name = entry.jockey
            results_fetched_at = None
        else:
            name = jockey.name if jockey else None
            results_fetched_at = _iso(jockey.results_fetched_at) if jockey else None

        return {
            "jockey_id": jockey_id,
            "name": name,
            "results_fetched_at": results_fetched_at,
            "results": [
                {
                    "race_key": r.race_key,
                    "race_date": r.race_date.isoformat() if r.race_date else None,
                    "venue": r.venue,
                    "race_name": r.race_name,
                    "field_size": r.field_size,
                    "horse_id": r.horse_id,
                    "horse_name": r.horse_name,
                    "horse_number": r.horse_number,
                    "trainer": r.trainer,
                    "trainer_id": r.trainer_id,
                    "weight": r.weight,
                    "odds": r.odds,
                    "popularity": r.popularity,
                    "finish_position": r.finish_position,
                    "distance": r.distance,
                    "track_type": r.track_type,
                    "going": r.going,
                }
                for r in results
            ],
        }
    finally:
        session.close()


@app.get("/api/trainers/{trainer_id}")
def trainer_detail(trainer_id: str) -> dict:
    session = get_session()
    try:
        trainer = session.get(Trainer, trainer_id)
        results = (
            session.query(TrainerResult)
            .filter(TrainerResult.trainer_id == trainer_id)
            .order_by(TrainerResult.race_date.desc().nullslast(), TrainerResult.id.desc())
            .limit(50)
            .all()
        )
        if trainer is None and not results:
            entry = session.query(Entry).filter(Entry.trainer_id == trainer_id).first()
            if entry is None:
                raise HTTPException(status_code=404, detail="trainer not found")
            name = entry.trainer
            results_fetched_at = None
        else:
            name = trainer.name if trainer else None
            results_fetched_at = _iso(trainer.results_fetched_at) if trainer else None

        return {
            "trainer_id": trainer_id,
            "name": name,
            "results_fetched_at": results_fetched_at,
            "results": [
                {
                    "race_key": r.race_key,
                    "race_date": r.race_date.isoformat() if r.race_date else None,
                    "venue": r.venue,
                    "race_name": r.race_name,
                    "field_size": r.field_size,
                    "horse_id": r.horse_id,
                    "horse_name": r.horse_name,
                    "horse_number": r.horse_number,
                    "jockey": r.jockey,
                    "jockey_id": r.jockey_id,
                    "weight": r.weight,
                    "odds": r.odds,
                    "popularity": r.popularity,
                    "finish_position": r.finish_position,
                    "distance": r.distance,
                    "track_type": r.track_type,
                    "going": r.going,
                }
                for r in results
            ],
        }
    finally:
        session.close()


@app.get("/api/bets")
def list_bets(request: Request, mode: str = BettingMode.SIM.value) -> dict:
    if mode not in (BettingMode.SIM.value, BettingMode.PROD.value):
        raise HTTPException(status_code=400, detail="mode は 'sim' か 'prod' を指定してください")
    if mode == BettingMode.PROD.value and not _is_admin_request(request):
        require_admin(request)

    session = get_session()
    try:
        bets = (
            session.query(Bet)
            .options(selectinload(Bet.race), selectinload(Bet.entry))
            .filter(Bet.mode == mode)
            .order_by(Bet.placed_at.desc())
            .all()
        )

        items = []
        for b in bets:
            items.append(
                {
                    "id": b.id,
                    "race_id": b.race_id,
                    "race_date": _iso(b.race.race_date) if b.race else None,
                    "venue": b.race.venue if b.race else None,
                    "race_number": b.race.race_number if b.race else None,
                    "race_name": b.race.race_name if b.race else None,
                    "horse_number": b.entry.horse_number if b.entry else None,
                    "horse_name": b.entry.horse_name if b.entry else None,
                    "combination": b.combination,
                    "bet_type": b.bet_type,
                    "status": b.status,
                    "amount": b.amount,
                    "odds_at_bet": b.odds_at_bet,
                    "payout": b.payout,
                    "is_settled": b.is_settled,
                    "placed_at": _iso(b.placed_at),
                }
            )

        # 決済済みの賭けを時系列に並べた累積の投資額・回収額(回収率の推移グラフ用)
        cumulative = []
        invested = 0.0
        payout = 0.0
        settled = [
            b
            for b in sorted(bets, key=lambda b: b.placed_at or datetime.min)
            if b.is_settled and b.status == BetStatus.PLACED.value
        ]
        for b in settled:
            invested += b.amount
            payout += b.payout or 0
            cumulative.append(
                {
                    "placed_at": _iso(b.placed_at),
                    "invested": invested,
                    "payout": payout,
                    "recovery_rate": (payout / invested * 100) if invested else None,
                }
            )

        return {"stats": _bet_stats(bets), "bets": items, "cumulative": cumulative}
    finally:
        session.close()


@app.get("/api/jobs", dependencies=[Depends(require_admin)])
def list_jobs(limit: int = 50) -> dict:
    session = get_session()
    try:
        runs = (
            session.query(JobRun)
            .order_by(JobRun.created_at.desc())
            .limit(min(limit, 200))
            .all()
        )
        return {"jobs": [_job_to_dict(run) for run in runs], "scheduled_jobs": scheduled_jobs_view()}
    finally:
        session.close()


@app.put("/api/jobs/schedule", dependencies=[Depends(require_admin)])
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


@app.post("/api/jobs/{job_name}/run", dependencies=[Depends(require_admin)])
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


@app.get("/api/settings", dependencies=[Depends(require_admin)])
def read_settings() -> dict:
    return get_settings_view()


@app.put("/api/settings", dependencies=[Depends(require_admin)])
def update_settings(values: dict) -> dict:
    try:
        return save_settings(values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ビルド済みフロントエンドの配信(/api 以外のパス)
WEBUI_DIST = Path(__file__).resolve().parents[2] / "webui" / "dist"
if WEBUI_DIST.exists():

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEBUI_DIST / "index.html")

    @app.get("/horses/{horse_id}")
    def horse_page(horse_id: str) -> FileResponse:
        return FileResponse(WEBUI_DIST / "index.html")

    @app.get("/jockeys/{jockey_id}")
    def jockey_page(jockey_id: str) -> FileResponse:
        return FileResponse(WEBUI_DIST / "index.html")

    @app.get("/trainers/{trainer_id}")
    def trainer_page(trainer_id: str) -> FileResponse:
        return FileResponse(WEBUI_DIST / "index.html")

    app.mount("/", StaticFiles(directory=WEBUI_DIST, html=True), name="webui")
else:
    logger.warning("webui/dist が見つかりません。フロントエンドは配信されません。")
