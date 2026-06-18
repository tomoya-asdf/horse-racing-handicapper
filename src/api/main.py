"""WebUI用のバックエンドAPI(FastAPI)。

システム全体の状況・履歴の参照、ジョブの手動実行、設定変更を提供する。
ジョブの実行自体は行わず、job_runs テーブルへの登録のみ行い、
担当サービス(collector/predictor)がポーリングして実行する。
ビルド済みのフロントエンド(webui/dist)も同じプロセスから配信する。
"""

import logging
import json
import secrets
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import extract, func
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
    HorsePedigree,
    HorseResult,
    JobRun,
    KaisaiDate,
    ModelVersion,
    Prediction,
    Race,
    RaceCollectionStatus,
)
from src.common.paths import MODEL_PATH
from src.common.timeutils import JST, now_jst
from src.predictor import betting

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
    by_type = {}
    for bet_type in sorted({b.bet_type for b in bets}):
        type_bets = [b for b in bets if b.bet_type == bet_type]
        type_placed = [b for b in type_bets if b.status == BetStatus.PLACED.value]
        type_settled = [b for b in type_placed if b.is_settled]
        type_invested = sum(b.amount for b in type_settled)
        type_payout = sum(b.payout or 0 for b in type_settled)
        by_type[bet_type] = {
            "invested": type_invested,
            "payout": type_payout,
            "recovery_rate": (type_payout / type_invested * 100) if type_invested else None,
            "settled_count": len(type_settled),
            "unsettled_count": len(type_placed) - len(type_settled),
            "pending_count": sum(1 for b in type_bets if b.status == BetStatus.PENDING.value),
            "dry_run_count": sum(1 for b in type_bets if b.status == BetStatus.DRY_RUN.value),
            "failed_count": sum(1 for b in type_bets if b.status == BetStatus.FAILED.value),
        }
    return {
        "invested": invested,
        "payout": payout,
        "recovery_rate": (payout / invested * 100) if invested else None,
        "settled_count": len(settled),
        "unsettled_count": len(placed) - len(settled),
        "pending_count": sum(1 for b in bets if b.status == BetStatus.PENDING.value),
        "dry_run_count": sum(1 for b in bets if b.status == BetStatus.DRY_RUN.value),
        "failed_count": sum(1 for b in bets if b.status == BetStatus.FAILED.value),
        "by_type": by_type,
    }


def _job_to_dict(run: JobRun) -> dict:
    return {
        "id": run.id,
        "job_name": run.job_name,
        "label": JOB_LABELS.get(run.job_name, run.job_name),
        "trigger": run.trigger,
        "status": run.status,
        "detail": run.detail,
        "params": run.params,
        "created_at": _iso(run.created_at),
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
    }


def _reservation_to_dict(reservation: dict) -> dict:
    return {
        **reservation,
        "label": JOB_LABELS.get(reservation["job_name"], reservation["job_name"]),
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
    latest_model = None
    if session is not None:
        latest_model = session.query(ModelVersion).order_by(ModelVersion.trained_at.desc()).first()
    if not MODEL_PATH.exists():
        version = latest_model.version if latest_model else (
            _latest_prediction_model_version(session) if session is not None else None
        )
        return {
            "trained": bool(version),
            "version": version,
            "trained_at": _iso(latest_model.trained_at) if latest_model else None,
        }
    info = {
        "trained": True,
        "version": latest_model.version if latest_model else (
            _latest_prediction_model_version(session) if session is not None else None
        ),
        "trained_at": _iso(latest_model.trained_at) if latest_model else None,
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
        except ModuleNotFoundError as exc:
            logger.info("model bundle metadata unavailable in api image: %s", exc)
        except Exception as exc:
            logger.warning("failed to read model bundle metadata: %s", exc)
    return info


def _json_value(raw: str | None, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default


def _model_version_to_dict(model_version: ModelVersion) -> dict:
    return {
        "version": model_version.version,
        "trained_at": _iso(model_version.trained_at),
        "race_count": model_version.race_count,
        "row_count": model_version.row_count,
        "valid_race_count": model_version.valid_race_count,
        "auc": model_version.auc,
        "logloss": model_version.logloss,
        "n_estimators": model_version.n_estimators,
        "calibrated": bool(model_version.calibrated),
        "feature_columns": _json_value(model_version.feature_columns, []),
        "categorical_features": _json_value(model_version.categorical_features, []),
        "feature_importances": _json_value(model_version.feature_importances, []),
        "metrics": _json_value(model_version.metrics, {}),
        "training_params": _json_value(model_version.training_params, {}),
        "model_path": model_version.model_path,
    }


def _model_bundle_to_dict(bundle: dict, trained_at: datetime | None = None) -> dict:
    return {
        "version": bundle.get("version"),
        "trained_at": _iso(trained_at),
        "race_count": None,
        "row_count": None,
        "valid_race_count": None,
        "auc": None,
        "logloss": None,
        "n_estimators": None,
        "calibrated": bool(bundle.get("calibrator")),
        "feature_columns": bundle.get("feature_columns", []),
        "categorical_features": bundle.get("categorical_features", []),
        "feature_importances": [],
        "metrics": {},
        "training_params": {},
        "model_path": str(MODEL_PATH),
    }


def _minimal_model_version_dict(version: str) -> dict:
    return {
        "version": version,
        "trained_at": None,
        "race_count": None,
        "row_count": None,
        "valid_race_count": None,
        "auc": None,
        "logloss": None,
        "n_estimators": None,
        "calibrated": False,
        "feature_columns": [],
        "categorical_features": [],
        "feature_importances": [],
        "metrics": {},
        "training_params": {},
        "model_path": None,
    }


def _current_model_bundle_dict() -> dict | None:
    if not MODEL_PATH.exists():
        return None
    try:
        import joblib

        trained_at = datetime.fromtimestamp(MODEL_PATH.stat().st_mtime)
        return _model_bundle_to_dict(joblib.load(MODEL_PATH), trained_at)
    except ModuleNotFoundError as exc:
        logger.info("model bundle metadata unavailable in api image: %s", exc)
        return None
    except Exception as exc:
        logger.warning("failed to read model bundle metadata: %s", exc)
        return None


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
            session.query(func.count(func.distinct(Entry.horse_id)))
            .join(HorseResult, HorseResult.horse_id == Entry.horse_id)
            .filter(Entry.horse_id.isnot(None), Entry.horse_id != "")
            .scalar()
            or 0
        )
        horse_target_count = (
            session.query(func.count(func.distinct(Entry.horse_id)))
            .filter(Entry.horse_id.isnot(None), Entry.horse_id != "")
            .scalar()
            or 0
        )
        horse_uncollected_count = max(horse_target_count - horse_result_horse_count, 0)
        last_collected_at = session.query(func.max(Race.created_at)).scalar()
        upcoming_race_count = (
            session.query(func.count(Race.id))
            .filter(Race.start_time.isnot(None), Race.start_time > now_jst())
            .scalar()
            or 0
        )
        predicted_upcoming_race_count = (
            session.query(func.count(func.distinct(Prediction.race_id)))
            .join(Race, Race.id == Prediction.race_id)
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
            "horse_target_count": horse_target_count,
            "horse_uncollected_count": horse_uncollected_count,
            "upcoming_race_count": upcoming_race_count,
            "predicted_upcoming_race_count": predicted_upcoming_race_count,
            "last_collected_at": _iso(last_collected_at),
        },
        "modes": modes,
        "latest_jobs": latest_jobs,
        "settings": get_settings_view(include_env=False),
    }


@app.get("/api/models")
def list_models(limit: int = 30) -> dict:
    session = get_session()
    try:
        rows = (
            session.query(ModelVersion)
            .order_by(ModelVersion.trained_at.desc(), ModelVersion.version.desc())
            .limit(min(max(limit, 1), 100))
            .all()
        )
        models = [_model_version_to_dict(row) for row in rows]
        current = _current_model_bundle_dict()
        if current and not any(row["version"] == current["version"] for row in models):
            models.insert(0, current)
        known_versions = {row["version"] for row in models}
        prediction_versions = (
            session.query(Prediction.model_version)
            .filter(Prediction.model_version.isnot(None))
            .distinct()
            .order_by(Prediction.model_version.desc())
            .limit(100)
            .all()
        )
        for (version,) in prediction_versions:
            if version not in known_versions:
                models.append(_minimal_model_version_dict(version))
                known_versions.add(version)
        return {"models": models[: min(max(limit, 1), 100)]}
    finally:
        session.close()


@app.get("/api/models/{version}")
def model_detail(version: str) -> dict:
    session = get_session()
    try:
        row = session.get(ModelVersion, version)
        if row is None:
            current = _current_model_bundle_dict()
            if current and current["version"] == version:
                return current
            exists = (
                session.query(Prediction.id)
                .filter(Prediction.model_version == version)
                .first()
            )
            if exists is not None:
                return _minimal_model_version_dict(version)
            raise HTTPException(status_code=404, detail="model version not found")
        return _model_version_to_dict(row)
    finally:
        session.close()


@app.get("/api/models/{version}/calibration")
def model_calibration(version: str, bins: int = 10) -> dict:
    """本番予測の確率較正(キャリブレーション)を集計して返す。

    このバージョンが予測した確定済みレースについて、予測スコア(=1着になる確率)を
    分位で ``bins`` 個のビンに分け、各ビンの「平均予測確率」と「実際の1着率」を返す。
    予測確率が正確なら両者は一致する(プロット上で対角線に乗る)。
    スコア分布は1着率が低い側に偏るため、等幅でなく分位でビン分割する。
    """
    bins = min(max(bins, 2), 20)
    session = get_session()
    try:
        rows = (
            session.query(Prediction.score, Entry.finish_position)
            .join(Entry, Prediction.entry_id == Entry.id)
            .filter(
                Prediction.model_version == version,
                Prediction.score.isnot(None),
                Entry.finish_position.isnot(None),
            )
            .all()
        )
        pairs = sorted(
            ((float(score), 1 if finish == 1 else 0) for score, finish in rows),
            key=lambda p: p[0],
        )
        total = len(pairs)
        wins_total = sum(won for _, won in pairs)
        result_bins = []
        bin_count = max(1, min(bins, total))
        for i in range(bin_count):
            start = i * total // bin_count
            end = (i + 1) * total // bin_count
            group = pairs[start:end]
            if not group:
                continue
            scores = [score for score, _ in group]
            wins = sum(won for _, won in group)
            result_bins.append(
                {
                    "mean_predicted": sum(scores) / len(group),
                    "actual_rate": wins / len(group),
                    "count": len(group),
                    "score_min": scores[0],
                    "score_max": scores[-1],
                }
            )
        return {
            "version": version,
            "sample_count": total,
            "win_count": wins_total,
            "base_rate": (wins_total / total) if total else None,
            "bins": result_bins,
        }
    finally:
        session.close()


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
    trainer: str | None = None,
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
        if trainer:
            query = query.filter(Race.entries.any(Entry.trainer.ilike(f"%{trainer.strip()}%")))
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
                selectinload(Race.odds),
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


@app.get("/api/race-dates")
def race_dates() -> dict:
    """カレンダー表示用の日付一覧を返す。

    - ``collected``: レースデータが存在する日(収集済み)
    - ``scheduled``: netkeibaの開催カレンダー上の開催日(``kaisai_dates``)

    レース一覧画面のカレンダーで、収集済み(色付け)と「開催予定だが未収集」
    (別色)を区別するために両方返す。
    """
    session = get_session()
    try:
        collected = [
            row[0].isoformat()
            for row in session.query(Race.race_date)
            .filter(Race.race_date.isnot(None))
            .distinct()
            .order_by(Race.race_date)
            .all()
        ]
        scheduled = [
            row[0].isoformat()
            for row in session.query(KaisaiDate.kaisai_date)
            .order_by(KaisaiDate.kaisai_date)
            .all()
        ]
        return {"collected": collected, "scheduled": scheduled}
    finally:
        session.close()


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
                selectinload(Race.odds),
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
        collected_kinds = {
            kind
            for (kind,) in session.query(RaceCollectionStatus.kind).filter(
                RaceCollectionStatus.race_id == race_id
            )
        }
        collection_status = {
            "horse_results": "horse_results" in collected_kinds,
        }
        visible_bets = [b for b in race.bets if is_admin or b.mode != BettingMode.PROD.value]
        bet_entry_ids = {b.entry_id for b in visible_bets}
        betting_config = load_betting_config()
        ai_rank_map = _rank_entries(score_map, reverse=True)
        value_odds_map = {
            e.id: e.pre_race_odds if e.pre_race_odds is not None else e.odds
            for e in race.entries
        }
        odds_rank_map = _rank_entries(
            {
                e.id: value
                for e in race.entries
                if (value := value_odds_map[e.id]) is not None and value > 0
            }
        )
        entry_count = len(race.entries)
        pair_count = entry_count * (entry_count - 1) // 2
        odds_rows_by_type: dict[str, set[str]] = {}
        for row in race.odds:
            odds_rows_by_type.setdefault(row.bet_type, set()).add(row.combination)
        odds_status = [
            {
                "bet_type": "単勝",
                "available": sum(
                    1
                    for e in race.entries
                    if (e.pre_race_odds is not None and e.pre_race_odds > 0)
                    or (e.odds is not None and e.odds > 0)
                ),
                "total": entry_count,
            },
            {
                "bet_type": "複勝",
                "available": len(odds_rows_by_type.get("複勝", set())),
                "total": entry_count,
            },
            {
                "bet_type": "馬連",
                "available": len(odds_rows_by_type.get("馬連", set())),
                "total": pair_count,
            },
            {
                "bet_type": "ワイド",
                "available": len(odds_rows_by_type.get("ワイド", set())),
                "total": pair_count,
            },
        ]

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
                "odds": value_odds_map[e.id],
                "odds_rank": odds_rank_map.get(e.id),
                "expected_value": (
                    score_map[e.id] * value_odds_map[e.id]
                    if value_odds_map[e.id] is not None and value_odds_map[e.id] > 0
                    else None
                ),
            }
            for e in ranked_entries[:3]
        ]
        candidate_entries = {e.id: e for e in race.entries}
        bet_candidates = [
            {
                "bet_type": c.bet_type,
                "entry_id": c.entry_id,
                "horse_number": candidate_entries[c.entry_id].horse_number
                if c.entry_id in candidate_entries
                else None,
                "horse_name": candidate_entries[c.entry_id].horse_name
                if c.entry_id in candidate_entries
                else None,
                "combination": c.combination,
                "probability": c.probability,
                "odds": c.odds,
                "expected_value": c.expected_value,
            }
            for c in betting.build_bet_candidates(
                race,
                [p for p in race.predictions if p.model_version == model_version],
                betting_config,
            )
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
                "pre_race_odds": e.pre_race_odds,
                "final_odds": e.final_odds,
                "popularity": e.popularity if e.popularity is not None else odds_rank_map.get(e.id),
                "finish_position": e.finish_position,
                "score": score_map.get(e.id),
                "ai_rank": ai_rank_map.get(e.id),
                "odds_rank": odds_rank_map.get(e.id),
                "expected_value": (
                    score_map[e.id] * value_odds_map[e.id]
                    if e.id in score_map
                    and value_odds_map[e.id] is not None
                    and value_odds_map[e.id] > 0
                    else None
                ),
                "value_label": (
                    "妙味あり"
                    if e.id in score_map
                    and value_odds_map[e.id] is not None
                    and value_odds_map[e.id] > 0
                    and score_map[e.id] * value_odds_map[e.id] >= betting_config.min_expected_value
                    else "見送り"
                    if e.id in score_map
                    and value_odds_map[e.id] is not None
                    and value_odds_map[e.id] > 0
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
                "model_version": b.model_version,
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
                "odds_status": odds_status,
            },
            "entries": entries,
            "bet_candidates": bet_candidates,
            "bets": bets,
            "collection_status": collection_status,
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

        pedigree = (
            session.query(HorsePedigree)
            .filter(HorsePedigree.horse_id == horse_id)
            .order_by(HorsePedigree.generation.asc(), HorsePedigree.position.asc())
            .all()
        )

        return {
            "horse_id": horse_id,
            "name": name,
            "sire_id": sire_id,
            "sire_name": sire_name,
            "results_fetched_at": results_fetched_at,
            "pedigree": [
                {
                    "generation": p.generation,
                    "position": p.position,
                    "ancestor_horse_id": p.ancestor_horse_id,
                    "ancestor_name": p.ancestor_name,
                }
                for p in pedigree
            ],
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
                    "jockey_id": r.jockey_id,
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


def _person_detail(
    id_attr: str, name_attr: str, partner_attr: str, person_id: str, year: int | None = None
) -> dict:
    """騎手/調教師の戦績を、収集済みの出走表(entries × races)から構成して返す。

    騎手/調教師の過去成績は個別ページをスクレイプせず、自前に蓄積した出走データから
    そのまま組み立てる(特徴量も同じ entries から作る)。

    戦績は年度(``year``)単位で返す。指定が無い/未収集の年度なら最新年度を使い、
    収集済みの全年度を ``years`` として返してUI側で切り替えられるようにする。
    """
    session = get_session()
    try:
        person_col = getattr(Entry, id_attr)
        year_col = extract("year", Race.race_date)
        year_rows = (
            session.query(year_col)
            .select_from(Entry)
            .join(Race, Race.id == Entry.race_id)
            .filter(person_col == person_id, Race.race_date.isnot(None))
            .distinct()
            .all()
        )
        years = sorted({int(row[0]) for row in year_rows if row[0] is not None}, reverse=True)
        if not years:
            raise HTTPException(status_code=404, detail=f"{name_attr} not found")
        selected_year = year if year in years else years[0]

        entries = (
            session.query(Entry)
            .join(Race, Race.id == Entry.race_id)
            .options(selectinload(Entry.race).selectinload(Race.entries))
            .filter(person_col == person_id, year_col == selected_year)
            .order_by(Race.race_date.desc().nullslast(), Entry.id.desc())
            .all()
        )
        name = next((getattr(e, name_attr) for e in entries if getattr(e, name_attr)), None)

        results = []
        for e in entries:
            r = e.race
            results.append(
                {
                    "race_key": r.race_key,
                    "race_date": r.race_date.isoformat() if r.race_date else None,
                    "venue": r.venue,
                    "race_name": r.race_name,
                    "field_size": len(r.entries) if r.entries else None,
                    "horse_id": e.horse_id,
                    "horse_name": e.horse_name,
                    "horse_number": e.horse_number,
                    partner_attr: getattr(e, partner_attr),
                    f"{partner_attr}_id": getattr(e, f"{partner_attr}_id"),
                    "weight": e.weight,
                    "odds": e.odds,
                    "popularity": e.popularity,
                    "finish_position": e.finish_position,
                    "distance": r.distance,
                    "track_type": r.track_type,
                    "going": r.going,
                }
            )
        return {
            f"{name_attr}_id": person_id,
            "name": name,
            "results_fetched_at": None,
            "years": years,
            "selected_year": selected_year,
            "results": results,
        }
    finally:
        session.close()


@app.get("/api/jockeys/{jockey_id}")
def jockey_detail(jockey_id: str, year: int | None = None) -> dict:
    return _person_detail("jockey_id", "jockey", "trainer", jockey_id, year)


@app.get("/api/trainers/{trainer_id}")
def trainer_detail(trainer_id: str, year: int | None = None) -> dict:
    return _person_detail("trainer_id", "trainer", "jockey", trainer_id, year)


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
                    "model_version": b.model_version,
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
        latest_jobs = []
        for job_name in jobs.ALL_JOBS:
            run = (
                session.query(JobRun)
                .filter(JobRun.job_name == job_name)
                .order_by(JobRun.created_at.desc().nullslast(), JobRun.id.desc())
                .first()
            )
            if run:
                latest_jobs.append(_job_to_dict(run))
        return {
            "jobs": [_job_to_dict(run) for run in runs],
            "latest_jobs": latest_jobs,
            "scheduled_jobs": scheduled_jobs_view(),
            "reservations": [
                _reservation_to_dict(row) for row in jobs.list_reservations(limit=100)
            ],
        }
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


def _parse_reservation_run_at(value: object) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="実行日時を指定してください")
    try:
        run_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="実行日時の形式が不正です")
    if run_at.tzinfo is not None:
        run_at = run_at.astimezone(JST).replace(tzinfo=None)
    if run_at <= now_jst():
        raise HTTPException(status_code=400, detail="実行日時は未来の日時を指定してください")
    return run_at


def _validate_reservation_params(job_name: str, body: dict) -> dict | None:
    params = body.get("params")
    if job_name == jobs.BACKFILL:
        return _validate_backfill_params(params if isinstance(params, dict) else {})
    if job_name == jobs.BACKTEST:
        return _validate_backtest_params(params if isinstance(params, dict) else {})
    return params if isinstance(params, dict) and params else None


@app.post("/api/job-reservations", dependencies=[Depends(require_admin)])
def create_job_reservation(body: dict) -> dict:
    job_name = str(body.get("job_name", "")).strip()
    if job_name not in jobs.ALL_JOBS:
        raise HTTPException(status_code=400, detail=f"未対応のジョブです: {job_name}")
    run_at = _parse_reservation_run_at(body.get("run_at"))
    params = _validate_reservation_params(job_name, body)
    reservation = jobs.reserve(job_name, run_at, params)
    return _reservation_to_dict(reservation)


@app.post("/api/job-reservations/{reservation_id}/cancel", dependencies=[Depends(require_admin)])
def cancel_job_reservation(reservation_id: int) -> dict:
    if not jobs.cancel_reservation(reservation_id):
        raise HTTPException(status_code=409, detail="キャンセルできる予約が見つかりません")
    return {"cancelled": True, "id": reservation_id}


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


@app.get("/api/jobs/{run_id}", dependencies=[Depends(require_admin)])
def job_detail(run_id: int) -> dict:
    session = get_session()
    try:
        run = session.get(JobRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="job run not found")
        return _job_to_dict(run)
    finally:
        session.close()


@app.post("/api/jobs/{run_id}/stop", dependencies=[Depends(require_admin)])
def stop_job(run_id: int) -> dict:
    if not jobs.stop_queued(run_id):
        raise HTTPException(
            status_code=409,
            detail="停止できるのは実行待ち(queued)のジョブのみです。実行中ジョブは安全に中断できません。",
        )
    return {"stopped": True, "id": run_id}


@app.post("/api/system/restart", dependencies=[Depends(require_admin)])
def restart_system() -> dict:
    docker = shutil.which("docker")
    compose_file = Path("/app/docker-compose.yml")
    if docker is None or not compose_file.exists():
        raise HTTPException(
            status_code=409,
            detail=(
                "Web UIコンテナからDocker Composeを操作できない構成です。"
                "ホスト側で `docker compose restart collector predictor webui` を実行してください。"
            ),
        )
    result = subprocess.run(
        [docker, "compose", "-f", str(compose_file), "restart", "collector", "predictor", "webui"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=(result.stderr or result.stdout or "restart failed")[:1000])
    return {"restarted": True, "detail": result.stdout}


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

    @app.get("/models")
    def models_page() -> FileResponse:
        return FileResponse(WEBUI_DIST / "index.html")

    @app.get("/models/{version}")
    def model_page(version: str) -> FileResponse:
        return FileResponse(WEBUI_DIST / "index.html")

    app.mount("/", StaticFiles(directory=WEBUI_DIST, html=True), name="webui")
else:
    logger.warning("webui/dist が見つかりません。フロントエンドは配信されません。")
