"""Backfill historical races, entries, odds, and race results."""

import logging
import sys
from datetime import date, datetime, timedelta

from src.collector import scraper
from src.collector.main import _upsert_races, collect_kaisai_dates
from src.common.db import get_session, init_db
from src.common.models import Race
from src.common.timeutils import now_jst

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _apply_results(start: date, end: date) -> int:
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
    # 開催カレンダーを先に取得・保存し、開催日にだけ出馬表・結果を取りに行く。
    # 長期間のバックフィルでは開催の無い平日への無駄なリクエストを大幅に削減できる。
    kaisai = collect_kaisai_dates(start, end)
    total = 0
    updated = 0
    for offset in range((end - start).days + 1):
        target = start + timedelta(days=offset)
        if target not in kaisai:
            continue
        races = scraper.fetch_upcoming_races(target, include_started=True)
        _upsert_races(races)
        total += len(races)
        daily_updated = _apply_results(target, target)
        updated += daily_updated
        logger.info("%s: %d races collected, %d results applied", target, len(races), daily_updated)

    return f"取得レース={total}件({start}〜{end}, 開催{len(kaisai)}日), 結果反映={updated}件"


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: python -m src.collector.backfill <start YYYYMMDD> <end YYYYMMDD>")
        sys.exit(1)
    try:
        start = datetime.strptime(sys.argv[1], "%Y%m%d").date()
        end = datetime.strptime(sys.argv[2], "%Y%m%d").date()
    except ValueError:
        print("dates must be YYYYMMDD")
        sys.exit(1)
    if start > end:
        print("start date must be before or equal to end date")
        sys.exit(1)
    if end >= now_jst().date():
        print("backfill is only for past dates; use collect for today and future dates")
        sys.exit(1)

    init_db()
    logger.info(backfill(start, end))


if __name__ == "__main__":
    main()
