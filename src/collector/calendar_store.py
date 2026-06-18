"""netkeiba 開催カレンダーの取得・保存。

カレンダー(開催日)を月単位で取得して ``kaisai_dates`` テーブルへ保存し、
収集側が「開催日にだけ出馬表を取りに行く」ために使う開催日集合を返す。
"""

import logging
from datetime import date, timedelta

from src.collector import scraper
from src.common.db import get_session
from src.common.models import KaisaiDate
from src.common.timeutils import now_jst

logger = logging.getLogger(__name__)


def _store_kaisai_dates(dates: set) -> None:
    """netkeiba開催カレンダーの開催日を kaisai_dates テーブルへ反映する(冪等)。"""
    if not dates:
        return
    session = get_session()
    try:
        existing = {
            row[0]
            for row in session.query(KaisaiDate.kaisai_date)
            .filter(KaisaiDate.kaisai_date.in_(dates))
            .all()
        }
        now = now_jst()
        for d in dates:
            if d in existing:
                continue
            session.add(KaisaiDate(kaisai_date=d, fetched_at=now))
        session.commit()
    finally:
        session.close()


def collect_kaisai_dates(start, end) -> frozenset:
    """``start``〜``end`` の各年月の開催カレンダーを取得してDBへ保存し、範囲内の開催日集合を返す。

    カレンダーは月単位で取得し、取得できた月は**その月全体**の開催日を保存する
    (カレンダー上は開催日だが未収集、の判別に使うため)。月の取得に失敗した場合は
    安全側に倒し、その月の全日付を「開催日候補」として返す(収集の取りこぼし防止)。
    """
    if start > end:
        return frozenset()
    in_range: set = set()
    to_store: set = set()
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        month_start = date(year, month, 1)
        next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
        month_end = date(next_year, next_month, 1) - timedelta(days=1)
        lo, hi = max(start, month_start), min(end, month_end)
        kaisai = scraper.fetch_kaisai_dates(year, month)
        if not kaisai:
            in_range.update(lo + timedelta(days=i) for i in range((hi - lo).days + 1))
        else:
            to_store.update(kaisai)
            in_range.update(d for d in kaisai if lo <= d <= hi)
        year, month = next_year, next_month
    _store_kaisai_dates(to_store)
    return frozenset(in_range)
