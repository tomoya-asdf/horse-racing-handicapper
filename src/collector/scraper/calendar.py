"""netkeiba 開催カレンダー(top/calendar.html)から開催日を取得する。"""

import logging
import re
import time
from datetime import date, datetime

import requests

from src.collector.scraper._core import BASE_URL, _get

logger = logging.getLogger(__name__)

# netkeibaの開催カレンダー(top/calendar.html)から開催日リンク(kaisai_date=YYYYMMDD)を拾う
_KAISAI_DATE_RE = re.compile(r"kaisai_date=(\d{8})")
# (year, month) -> (取得時刻, 開催日set)。collectが短間隔で回るため毎回取得せずキャッシュする
_CALENDAR_CACHE_TTL_SECONDS = 6 * 60 * 60
_calendar_cache: dict[tuple[int, int], tuple[float, frozenset[date]]] = {}


def fetch_kaisai_dates(year: int, month: int) -> frozenset[date]:
    """指定年月にJRA開催のある日付(date)の集合を返す。

    ``top/calendar.html`` を月1リクエストで取得し、開催日セルのリンクに含まれる
    ``kaisai_date=YYYYMMDD`` を集める。各日付へ個別リクエストする前にこの集合で
    絞り込むことで、開催の無い日へのリクエストを丸ごと省く(netkeiba負荷軽減)。
    プロセス内に短時間キャッシュする。取得失敗時は空集合を返す(呼び出し側で
    フォールバックする想定)。
    """
    cached = _calendar_cache.get((year, month))
    if cached is not None and time.perf_counter() - cached[0] < _CALENDAR_CACHE_TTL_SECONDS:
        return cached[1]
    dates: set[date] = set()
    try:
        response = _get(f"{BASE_URL}/top/calendar.html", params={"year": year, "month": month})
    except requests.RequestException as exc:
        logger.warning("failed to fetch calendar year=%s month=%s: %s", year, month, exc)
        return frozenset()
    for match in _KAISAI_DATE_RE.finditer(response.text):
        ymd = match.group(1)
        try:
            dates.add(datetime.strptime(ymd, "%Y%m%d").date())
        except ValueError:
            continue
    result = frozenset(dates)
    _calendar_cache[(year, month)] = (time.perf_counter(), result)
    return result
