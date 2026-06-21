"""スクレイパー共通基盤(HTTPクライアント・定数・低レベルパース補助)。

各機能モジュール(races/odds/results/horses/calendar/rendered)が
``from ._core import *`` で参照する。``__all__`` で公開する名前を明示する。
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass

import requests

from src.common.config import settings

logger = logging.getLogger(__name__)

# リクエスト送出の最小間隔を守るためのゲート。「リクエスト後に固定秒スリープ」だと
# 通信時間に加えて毎回まるごと待つうえ、最後の1件やキャッシュ的成功にも待ちが入る。
# 「直近の送出からの最小間隔」に置き換えることで通信時間を間隔に算入でき、無駄待ちを
# 減らせる。ロックで直列化するため、複数スレッドからの並列取得でも送出レート
# (= 1リクエスト / SCRAPER_REQUEST_INTERVAL_SECONDS)を保てる。
_rate_lock = threading.Lock()
_next_request_at = 0.0


def _throttle() -> None:
    interval = settings.SCRAPER_REQUEST_INTERVAL_SECONDS
    if interval <= 0:
        return
    global _next_request_at
    with _rate_lock:
        now = time.monotonic()
        wait = _next_request_at - now
        if wait > 0:
            time.sleep(wait)
            now += wait
        _next_request_at = now + interval

BASE_URL = "https://race.netkeiba.com"
# 馬の過去成績は db.netkeiba.com の馬ページにある(レース系とはホストが異なる)
DB_BASE_URL = "https://db.netkeiba.com"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# netkeibaのrace_idに含まれる場コード(JRA10場)
VENUE_CODES = {
    "01": "札幌",
    "02": "函館",
    "03": "福島",
    "04": "新潟",
    "05": "東京",
    "06": "中山",
    "07": "中京",
    "08": "京都",
    "09": "阪神",
    "10": "小倉",
}

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})


class _RetryableStatus(Exception):
    """一時的なHTTPステータス(5xx/429)を受け取ったことを表す内部例外。"""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"retryable status: {status_code}")
        self.status_code = status_code


@dataclass
class ScrapeMetrics:
    http_requests: int = 0
    playwright_pages: int = 0
    playwright_browser_starts: int = 0


_metrics = ScrapeMetrics()


def _metrics_snapshot() -> ScrapeMetrics:
    return ScrapeMetrics(
        http_requests=_metrics.http_requests,
        playwright_pages=_metrics.playwright_pages,
        playwright_browser_starts=_metrics.playwright_browser_starts,
    )


def _metrics_delta(start: ScrapeMetrics) -> ScrapeMetrics:
    return ScrapeMetrics(
        http_requests=_metrics.http_requests - start.http_requests,
        playwright_pages=_metrics.playwright_pages - start.playwright_pages,
        playwright_browser_starts=_metrics.playwright_browser_starts
        - start.playwright_browser_starts,
    )


def _log_metrics(label: str, started_at: float, start: ScrapeMetrics) -> None:
    delta = _metrics_delta(start)
    logger.info(
        "%s: elapsed=%.1fs, http_requests=%d, playwright_pages=%d, playwright_browser_starts=%d",
        label,
        time.perf_counter() - started_at,
        delta.http_requests,
        delta.playwright_pages,
        delta.playwright_browser_starts,
    )


# 一時的とみなしてリトライ対象にするHTTPステータス(サーバ側の過負荷・保護)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class NetkeibaHttpClient:
    def __init__(self) -> None:
        self.session = _session

    def get(self, url: str, **kwargs) -> requests.Response:
        # 一時的な失敗(接続断・タイムアウト・5xx/429)は指数バックオフで再試行する。
        # netkeibaのDOM変更等による恒久的失敗(404など)は即座に送出して気付けるようにする。
        attempts = settings.SCRAPER_MAX_RETRIES + 1
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                # 送出前に最小間隔を確保する(リトライも含め各送出を一定間隔に保つ)。
                _throttle()
                response = self.session.get(url, timeout=10, **kwargs)
                _metrics.http_requests += 1
                if response.status_code in _RETRYABLE_STATUS and attempt < attempts - 1:
                    raise _RetryableStatus(response.status_code)
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "").lower()
                if "text/html" in content_type and not re.search(r"charset=[\w-]", content_type):
                    # netkeibaは「Content-Type: text/html; charset=」とcharsetを空で返す。
                    # このときrequestsはencoding=""(不明)のままUTF-8で強制デコードして
                    # 文字化けする。さらにページによりUTF-8(レース一覧)とEUC-JP(出馬表・
                    # 結果)が混在するため、決め打ちせず内容から自動判定する
                    response.encoding = response.apparent_encoding
                return response
            except (requests.exceptions.RequestException, _RetryableStatus) as exc:
                last_exc = exc
                if attempt >= attempts - 1:
                    break
                backoff = settings.SCRAPER_RETRY_BACKOFF_SECONDS * (2**attempt)
                logger.warning(
                    "netkeiba request failed (attempt %d/%d), retrying in %.1fs: %s url=%s",
                    attempt + 1,
                    attempts,
                    backoff,
                    exc,
                    url,
                )
                time.sleep(backoff)
        assert last_exc is not None
        raise last_exc


_http = NetkeibaHttpClient()

BET_TYPE_WIN = "単勝"
BET_TYPE_PLACE = "複勝"
BET_TYPE_QUINELLA = "馬連"
BET_TYPE_WIDE = "ワイド"

JRA_ODDS_TYPES = {
    BET_TYPE_WIN: "1",
    BET_TYPE_PLACE: "2",
    BET_TYPE_QUINELLA: "4",
    BET_TYPE_WIDE: "5",
}


def _get(url: str, **kwargs) -> requests.Response:
    return _http.get(url, **kwargs)


def parse_race_key(race_key: str) -> dict:
    """netkeibaのrace_id(12桁: YYYY+場コード2桁+回2桁+日2桁+R2桁)を分解する。

    betting.py のIPATナビゲーションでも使用する。
    """
    if len(race_key) != 12 or not race_key.isdigit():
        raise ValueError(f"invalid race_key: {race_key!r}")
    venue_code = race_key[4:6]
    return {
        "year": race_key[0:4],
        "venue_code": venue_code,
        "venue": VENUE_CODES.get(venue_code, venue_code),
        "kai": int(race_key[6:8]),
        "day": int(race_key[8:10]),
        "race_number": int(race_key[10:12]),
    }


_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*発走")
_WEIGHT_RE = re.compile(r"^\d{2}\.\d$")
_RACE_ID_RE = re.compile(r"race_id=(\d{12})")
# 騎手ページのリンク(/jockey/result/recent/05339/ や /jockey/05339/)から騎手ID(数字)を取り出す
_JOCKEY_ID_RE = re.compile(r"/jockey/(?:result/recent/)?(\d+)")
# 調教師ページのリンク(/trainer/result/recent/01123/ や /trainer/01123/)から調教師IDを取り出す
_TRAINER_ID_RE = re.compile(r"/trainer/(?:result/recent/)?(\w+)")
# 馬ページのリンク(/horse/2019104567/)から馬IDを取り出す
_HORSE_ID_RE = re.compile(r"/horse/(\w+)")
_ODDS_RE = re.compile(r"^\d{1,4}\.\d$")
# 性齢("牡3"/"牝4"/"セ5")。騙馬は環境により"せ"表記もあるため両方拾う
_SEX_AGE_RE = re.compile(r"([牡牝セせ])\s*(\d+)")
# 馬体重("456(-12)"/"428(+4)"/"500(0)")。括弧内が前走比増減
_HORSE_WEIGHT_RE = re.compile(r"(\d{2,3})\s*\(\s*([+-]?\d+)\s*\)")
# 馬の過去成績の距離セル等で使う
_DISTANCE_RE = re.compile(r"([芝ダ障])\s*(\d{3,4})")
_TRACK_TYPE_MAP = {"芝": "芝", "ダ": "ダート", "障": "障害"}


def _parse_jockey_id(href: str | None) -> str | None:
    if not href:
        return None
    match = _JOCKEY_ID_RE.search(href)
    return match.group(1) if match else None


def _parse_trainer_id(href: str | None) -> str | None:
    if not href:
        return None
    match = _TRAINER_ID_RE.search(href)
    return match.group(1) if match else None


def _parse_sex_age(text: str) -> tuple[str | None, int | None]:
    """性齢セル('牡3')を性別と馬齢に分解する。取れない場合は (None, None)。"""
    match = _SEX_AGE_RE.search(text or "")
    if not match:
        return None, None
    sex = "セ" if match.group(1) == "せ" else match.group(1)
    return sex, int(match.group(2))


def _parse_horse_weight(text: str) -> tuple[int | None, int | None]:
    """馬体重セル('456(-12)')を体重と増減に分解する。

    括弧付き('456(-12)')は (456, -12)、増減のみ無い初出走('456')は (456, None)、
    '計不'等の未計量は (None, None)。
    """
    text = (text or "").strip()
    match = _HORSE_WEIGHT_RE.search(text)
    if match:
        return int(match.group(1)), int(match.group(2))
    only_weight = re.fullmatch(r"(\d{2,3})", text)
    if only_weight:
        return int(only_weight.group(1)), None
    return None, None


def _parse_horse_id(href: str | None) -> str | None:
    if not href:
        return None
    match = _HORSE_ID_RE.search(href)
    return match.group(1) if match else None


def _parse_float(text: str) -> float | None:
    """オッズ等の数値文字列をfloatにする。'---.-' 等の未確定プレースホルダはNone。"""
    text = text.strip()
    if not _ODDS_RE.match(text):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_odds_value(value) -> float | None:
    """APIのオッズ値をfloat化する。複勝/ワイドのレンジは下限で扱う。"""
    if isinstance(value, (list, tuple)) and value:
        value = value[0]
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    candidates = re.findall(r"\d+(?:\.\d+)?", text)
    if not candidates:
        return None
    try:
        odds = float(candidates[0])
    except ValueError:
        return None
    return odds if odds > 0 else None


def normalize_combination(numbers) -> str:
    """馬番の集合を昇順の買い目文字列にする(例 [9, 4] -> '4-9')。

    馬連オッズのキー・払戻の買い目・Bet.combination で同じ表記を使い、突き合わせ可能にする。
    """
    return "-".join(str(n) for n in sorted(int(x) for x in numbers))


__all__ = [
    "BASE_URL",
    "DB_BASE_URL",
    "USER_AGENT",
    "VENUE_CODES",
    "BET_TYPE_WIN",
    "BET_TYPE_PLACE",
    "BET_TYPE_QUINELLA",
    "BET_TYPE_WIDE",
    "JRA_ODDS_TYPES",
    "ScrapeMetrics",
    "_metrics",
    "_metrics_snapshot",
    "_metrics_delta",
    "_log_metrics",
    "NetkeibaHttpClient",
    "_http",
    "_get",
    "parse_race_key",
    "normalize_combination",
    "_parse_jockey_id",
    "_parse_trainer_id",
    "_parse_sex_age",
    "_parse_horse_weight",
    "_parse_horse_id",
    "_parse_float",
    "_parse_odds_value",
    "_TIME_RE",
    "_WEIGHT_RE",
    "_RACE_ID_RE",
    "_JOCKEY_ID_RE",
    "_TRAINER_ID_RE",
    "_HORSE_ID_RE",
    "_ODDS_RE",
    "_SEX_AGE_RE",
    "_HORSE_WEIGHT_RE",
    "_DISTANCE_RE",
    "_TRACK_TYPE_MAP",
]
