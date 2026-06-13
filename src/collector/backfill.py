"""過去レースの一括取得(バックフィル)。

モデル学習用のデータを増やすため、指定した開催日のレース、出馬表、
最終オッズ、確定結果に加えて、出走馬の過去戦績と血統も取得する。
"""

import logging
import sys
from datetime import date, datetime, timedelta

from src.collector import scraper
from src.collector.main import _upsert_races, update_horse_results_for_race_dates
from src.common.config import settings
from src.common.db import get_session, init_db
from src.common.models import Race
from src.common.timeutils import now_jst

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _apply_results(start: date, end: date) -> int:
    """期間内の発走済みレースに確定着順を反映し、反映できたレース数を返す。"""
    updated = 0
    session = get_session()
    try:
        races = session.query(Race).filter(Race.race_date >= start, Race.race_date <= end).all()
        for race in races:
            if not race.entries:
                continue
            if any(e.finish_position is not None for e in race.entries):
                continue
            if race.start_time is not None and race.start_time > now_jst():
                continue
            try:
                result = scraper.fetch_race_results(race.race_key)
            except Exception as exc:
                logger.warning("failed to fetch results for race_key=%s: %s", race.race_key, exc)
                continue

            positions = {e["horse_number"]: e["finish_position"] for e in result["entries"]}
            if not positions:
                logger.warning("no results parsed for race_key=%s", race.race_key)
                continue
            for entry in race.entries:
                if entry.horse_number in positions:
                    entry.finish_position = positions[entry.horse_number]
            updated += 1

        session.commit()
    finally:
        session.close()
    return updated


def backfill(start: date, end: date) -> str:
    total = 0
    updated = 0
    for offset in range((end - start).days + 1):
        target = start + timedelta(days=offset)
        races = scraper.fetch_upcoming_races(target, include_started=True)
        _upsert_races(races)
        total += len(races)
        daily_updated = _apply_results(target, target)
        updated += daily_updated
        logger.info("%s: %d races collected, %d results applied", target, len(races), daily_updated)

    horse_limit = settings.BACKFILL_HORSE_RESULTS_LIMIT
    horses = update_horse_results_for_race_dates(start, end, limit=horse_limit)
    return (
        f"取得レース={total}件({start}〜{end}), "
        f"結果反映={updated}件, 馬過去戦績/血統取得={horses}頭(上限{horse_limit}頭)"
    )


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: python -m src.collector.backfill <開始日YYYYMMDD> <終了日YYYYMMDD>")
        sys.exit(1)
    try:
        start = datetime.strptime(sys.argv[1], "%Y%m%d").date()
        end = datetime.strptime(sys.argv[2], "%Y%m%d").date()
    except ValueError:
        print("日付はYYYYMMDD形式で指定してください")
        sys.exit(1)
    if start > end:
        print("開始日は終了日以前を指定してください")
        sys.exit(1)
    if end >= now_jst().date():
        print("バックフィルは過去日付専用です。当日以降は通常の収集ジョブが対象です。")
        sys.exit(1)

    init_db()
    logger.info(backfill(start, end))


if __name__ == "__main__":
    main()
