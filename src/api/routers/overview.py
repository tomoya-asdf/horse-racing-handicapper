"""概要ページ用のサマリ API。"""

from fastapi import APIRouter, Request
from sqlalchemy import func

from src.api.deps import is_admin_request
from src.api.serializers import bet_stats, iso, job_to_dict, model_info
from src.common import jobs
from src.common.db import session_scope
from src.common.dynamic_config import get_settings_view
from src.common.models import (
    Bet,
    BettingMode,
    Entry,
    Horse,
    JobRun,
    Prediction,
    Race,
    RaceCollectionStatus,
)
from src.common.timeutils import now_jst

router = APIRouter()


@router.get("/api/overview")
def overview(request: Request) -> dict:
    is_admin = is_admin_request(request)
    with session_scope() as session:
        race_count = session.query(func.count(Race.id)).scalar() or 0
        finished_race_count = (
            session.query(func.count(func.distinct(Entry.race_id)))
            .filter(Entry.finish_position.isnot(None))
            .scalar()
            or 0
        )
        # 「収集済み」は過去成績テーブルの行有無ではなく Horse.results_fetched_at で判定する。
        # 新馬(過去走ゼロ)は horse_results 行を持たないため、行有無で数えると常に未収集に
        # 数えられてしまう。取得処理が走った馬(results_fetched_at が入っている)を収集済みとする。
        horse_result_horse_count = (
            session.query(func.count(func.distinct(Entry.horse_id)))
            .join(Horse, Horse.horse_id == Entry.horse_id)
            .filter(
                Entry.horse_id.isnot(None),
                Entry.horse_id != "",
                Horse.results_fetched_at.isnot(None),
            )
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
        # 戦績収集はレース起点で駆動する(各レースの全出走馬を集め切ったら収集済みに記録)。
        # 収集対象=全レース、収集済み=RaceCollectionStatus(kind=horse_results)の数。
        horse_collected_race_count = (
            session.query(func.count(RaceCollectionStatus.id))
            .filter(RaceCollectionStatus.kind == "horse_results")
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
            modes[mode] = bet_stats(bets)

        latest_jobs = []
        for job_name in jobs.ALL_JOBS:
            run = (
                session.query(JobRun)
                .filter(JobRun.job_name == job_name)
                .order_by(JobRun.created_at.desc())
                .first()
            )
            if run is not None:
                latest_jobs.append(job_to_dict(run))
        model_summary = model_info(session)

    return {
        "model": model_summary,
        "data": {
            "race_count": race_count,
            "finished_race_count": finished_race_count,
            "horse_result_horse_count": horse_result_horse_count,
            "horse_target_count": horse_target_count,
            "horse_uncollected_count": horse_uncollected_count,
            "horse_collected_race_count": horse_collected_race_count,
            "horse_target_race_count": race_count,
            "upcoming_race_count": upcoming_race_count,
            "predicted_upcoming_race_count": predicted_upcoming_race_count,
            "last_collected_at": iso(last_collected_at),
        },
        "modes": modes,
        "latest_jobs": latest_jobs,
        "settings": get_settings_view(include_env=False),
    }
