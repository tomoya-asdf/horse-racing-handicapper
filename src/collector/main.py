import logging
from datetime import timedelta

from apscheduler.schedulers.blocking import BlockingScheduler

from src.collector import scraper
from src.common import jobs
from src.common.config import settings
from src.common.db import get_session, init_db
from src.common.models import Entry, Race
from src.common.timeutils import now_jst

logging.basicConfig(level=logging.INFO)
# 5秒間隔のジョブポーリングがINFOログを埋め尽くすため、APSchedulerのログは抑制する
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# 結果の取得を試みる期間。これより古いレースは(開催中止等で結果が
# 取得できないままでも)対象から外し、再スクレイピングを打ち切る
RESULT_FETCH_DAYS = 7


def _upsert_races(races: list[dict]) -> None:
    session = get_session()
    try:
        for race_data in races:
            race = session.query(Race).filter_by(race_key=race_data["race_key"]).one_or_none()
            if race is None:
                race = Race(race_key=race_data["race_key"])
                session.add(race)

            race.race_date = race_data["race_date"]
            race.venue = race_data["venue"]
            race.race_number = race_data["race_number"]
            race.race_name = race_data["race_name"]
            race.start_time = race_data["start_time"]
            session.flush()  # 新規レースのIDを確定させる

            for entry_data in race_data["entries"]:
                entry = (
                    session.query(Entry)
                    .filter_by(race_id=race.id, horse_number=entry_data["horse_number"])
                    .one_or_none()
                )
                if entry is None:
                    entry = Entry(race_id=race.id, horse_number=entry_data["horse_number"])
                    session.add(entry)

                entry.horse_name = entry_data["horse_name"]
                entry.jockey = entry_data["jockey"]
                entry.weight = entry_data["weight"]
                entry.odds = entry_data["odds"]

        session.commit()
    finally:
        session.close()


def _update_finished_results() -> int:
    """確定したレースの着順をDBへ反映し、反映できたレース数を返す。"""
    updated = 0
    session = get_session()
    try:
        now = now_jst()
        races = (
            session.query(Race)
            .filter(
                Race.start_time.isnot(None),
                Race.start_time < now,
                Race.race_date >= (now - timedelta(days=RESULT_FETCH_DAYS)).date(),
            )
            .all()
        )
        for race in races:
            if not race.entries:
                continue
            # 一度でも結果を反映済みのレースはスキップする。出走取消・除外馬の
            # finish_position は確定後もNoneのままなので、all()での判定は不可
            if any(entry.finish_position is not None for entry in race.entries):
                continue

            try:
                result = scraper.fetch_race_results(race.race_key)
            except Exception as exc:
                logger.warning("failed to fetch results for race_key=%s: %s", race.race_key, exc)
                continue

            positions = {e["horse_number"]: e["finish_position"] for e in result["entries"]}
            if not positions:
                continue
            for entry in race.entries:
                if entry.horse_number in positions:
                    entry.finish_position = positions[entry.horse_number]
            updated += 1

        session.commit()
    finally:
        session.close()
    return updated


def _run_collect(params: dict) -> str:
    # JRAは主に土日開催のため、当日だけでなく数日先まで収集する
    # (開催の無い日はレース一覧が空で返るだけなので、リクエスト数は1日1回分増えるのみ)
    today = now_jst().date()
    total = 0
    for offset in range(settings.COLLECT_DAYS_AHEAD + 1):
        races = scraper.fetch_upcoming_races(today + timedelta(days=offset))
        _upsert_races(races)
        total += len(races)
    updated = _update_finished_results()
    return (
        f"取得レース={total}件"
        f"({today}〜{today + timedelta(days=settings.COLLECT_DAYS_AHEAD)}), "
        f"結果反映={updated}件"
    )


def _run_backfill(params: dict) -> str:
    """WebUIからの過去データ一括取得。paramsの日付範囲はAPI側で検証済み。"""
    from datetime import date

    from src.collector import backfill  # 循環import回避のため遅延import

    start = date.fromisoformat(params["start_date"])
    end = date.fromisoformat(params["end_date"])
    return backfill.backfill(start, end)


def _scheduled_collect() -> None:
    jobs.run_scheduled(jobs.COLLECT, _run_collect)


def _poll_queued_jobs() -> None:
    jobs.process_queued({jobs.COLLECT: _run_collect, jobs.BACKFILL: _run_backfill})


def main() -> None:
    init_db()
    jobs.recover_stale([jobs.COLLECT, jobs.BACKFILL])
    scheduler = BlockingScheduler(timezone="Asia/Tokyo")
    scheduler.add_job(
        _scheduled_collect, "interval", minutes=settings.COLLECT_INTERVAL_MINUTES
    )
    scheduler.add_job(
        _poll_queued_jobs, "interval", seconds=jobs.POLL_INTERVAL_SECONDS
    )
    logger.info("collector started: interval=%s min", settings.COLLECT_INTERVAL_MINUTES)
    _scheduled_collect()
    scheduler.start()


if __name__ == "__main__":
    main()
