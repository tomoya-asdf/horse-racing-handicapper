"""WebUI用のバックエンドAPI(FastAPI)。

システム全体の状況・履歴の参照、ジョブの手動実行、設定変更を提供する。
ジョブの実行自体は行わず、job_runs テーブルへの登録のみ行い、
担当サービス(collector/predictor)がポーリングして実行する。
ビルド済みのフロントエンド(webui/dist)も同じプロセスから配信する。
"""

import logging
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from src.common import jobs
from src.common.db import get_session, init_db
from src.common.dynamic_config import get_settings_view, save_settings
from src.common.models import Bet, BetStatus, BettingMode, Entry, JobRun, Prediction, Race
from src.common.timeutils import now_jst
from src.predictor.model import MODEL_PATH

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="競馬予測AI 管理API")

JOB_LABELS = {
    jobs.COLLECT: "データ収集",
    jobs.BACKFILL: "過去データ取得",
    jobs.PREDICT: "予測・賭け判断",
    jobs.SETTLE: "決済",
    jobs.TRAIN: "モデル学習",
}

# 一度にバックフィルできる最大日数(netkeibaへの負荷を抑えるため)
BACKFILL_MAX_DAYS = 31


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


def _model_info() -> dict:
    if not MODEL_PATH.exists():
        return {"trained": False, "version": None, "trained_at": None}
    info = {"trained": True, "version": None, "trained_at": None}
    try:
        import joblib

        bundle = joblib.load(MODEL_PATH)
        info["version"] = bundle.get("version")
    except Exception:
        logger.exception("failed to load model bundle")
    try:
        info["trained_at"] = datetime.fromtimestamp(MODEL_PATH.stat().st_mtime).isoformat()
    except OSError:
        pass
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


@app.get("/api/overview")
def overview() -> dict:
    session = get_session()
    try:
        race_count = session.query(func.count(Race.id)).scalar() or 0
        finished_race_count = (
            session.query(func.count(func.distinct(Entry.race_id)))
            .filter(Entry.finish_position.isnot(None))
            .scalar()
            or 0
        )
        last_collected_at = session.query(func.max(Race.created_at)).scalar()
        upcoming_race_count = (
            session.query(func.count(Race.id))
            .filter(Race.start_time.isnot(None), Race.start_time > now_jst())
            .scalar()
            or 0
        )

        modes = {}
        for mode in (BettingMode.SIM.value, BettingMode.PROD.value):
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
    finally:
        session.close()

    return {
        "model": _model_info(),
        "data": {
            "race_count": race_count,
            "finished_race_count": finished_race_count,
            "upcoming_race_count": upcoming_race_count,
            "last_collected_at": _iso(last_collected_at),
        },
        "modes": modes,
        "latest_jobs": latest_jobs,
        "settings": get_settings_view(),
    }


@app.get("/api/races")
def list_races(
    limit: int = 30,
    offset: int = 0,
    race_name: str | None = None,
    race_date: str | None = None,
    venue: str | None = None,
    status: str | None = None,
    horse_name: str | None = None,
    jockey: str | None = None,
    prediction: str | None = None,
    bet: str | None = None,
) -> dict:
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
        if venue:
            query = query.filter(Race.venue == venue.strip())
        if status == "finished":
            query = query.filter(Race.entries.any(Entry.finish_position.isnot(None)))
        elif status == "unfinished":
            query = query.filter(~Race.entries.any(Entry.finish_position.isnot(None)))
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
            query = query.filter(Race.bets.any())
        elif bet == "no":
            query = query.filter(~Race.bets.any())

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
                    "entry_count": len(race.entries),
                    "finished": any(e.finish_position is not None for e in race.entries),
                    "top_prediction": top,
                    "bet_count": len(race.bets),
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
def race_detail(race_id: int) -> dict:
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
        bet_entry_ids = {b.entry_id for b in race.bets}
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
                "horse_name": e.horse_name,
                "jockey": e.jockey,
                "weight": e.weight,
                "odds": e.odds,
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
                    if e.id in score_map and e.odds is not None and e.odds > 0 and score_map[e.id] * e.odds >= 1.0
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
                "amount": b.amount,
                "odds_at_bet": b.odds_at_bet,
                "payout": b.payout,
                "is_settled": b.is_settled,
                "placed_at": _iso(b.placed_at),
            }
            for b in race.bets
        ]
        return {
            "id": race.id,
            "race_key": race.race_key,
            "race_date": _iso(race.race_date),
            "venue": race.venue,
            "race_number": race.race_number,
            "race_name": race.race_name,
            "start_time": _iso(race.start_time),
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


@app.get("/api/bets")
def list_bets(mode: str = BettingMode.SIM.value) -> dict:
    if mode not in (BettingMode.SIM.value, BettingMode.PROD.value):
        raise HTTPException(status_code=400, detail="mode は 'sim' か 'prod' を指定してください")

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


@app.get("/api/jobs")
def list_jobs(limit: int = 50) -> dict:
    session = get_session()
    try:
        runs = (
            session.query(JobRun)
            .order_by(JobRun.created_at.desc())
            .limit(min(limit, 200))
            .all()
        )
        return {"jobs": [_job_to_dict(run) for run in runs]}
    finally:
        session.close()


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


@app.post("/api/jobs/{job_name}/run")
def trigger_job(job_name: str, body: dict | None = None) -> dict:
    if job_name not in jobs.ALL_JOBS:
        raise HTTPException(status_code=400, detail=f"未対応のジョブです: {job_name}")
    params = None
    if job_name == jobs.BACKFILL:
        params = _validate_backfill_params(body or {})
    result = jobs.enqueue(job_name, params)
    return {**result, "job_name": job_name, "label": JOB_LABELS[job_name]}


@app.get("/api/settings")
def read_settings() -> dict:
    return get_settings_view()


@app.put("/api/settings")
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

    app.mount("/", StaticFiles(directory=WEBUI_DIST, html=True), name="webui")
else:
    logger.warning("webui/dist が見つかりません。フロントエンドは配信されません。")
