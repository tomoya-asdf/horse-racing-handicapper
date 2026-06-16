"""netkeiba.com からレース情報・オッズ・結果を取得するスクレイパー。

netkeiba.com の公開ページ(race.netkeiba.com)を対象とする。HTMLの構造は
予告なく変更される可能性があるため、取得できない要素は警告ログを出して
スキップする等、できるだけ防御的にパースしている。サイトへの負荷軽減のため
リクエスト毎に ``settings.SCRAPER_REQUEST_INTERVAL_SECONDS`` 秒のスリープを挟む。
"""

from __future__ import annotations

import logging
import math
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as dt_time

import requests
from bs4 import BeautifulSoup

from src.common.config import settings
from src.common.timeutils import now_jst

logger = logging.getLogger(__name__)

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


class NetkeibaHttpClient:
    def __init__(self) -> None:
        self.session = _session

    def get(self, url: str, **kwargs) -> requests.Response:
        response = self.session.get(url, timeout=10, **kwargs)
        _metrics.http_requests += 1
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").lower()
        if "text/html" in content_type and not re.search(r"charset=[\w-]", content_type):
            # netkeibaは「Content-Type: text/html; charset=」とcharsetを空で返す。
            # このときrequestsはencoding=""(不明)のままUTF-8で強制デコードして
            # 文字化けする。さらにページによりUTF-8(レース一覧)とEUC-JP(出馬表・
            # 結果)が混在するため、決め打ちせず内容から自動判定する
            response.encoding = response.apparent_encoding
        time.sleep(settings.SCRAPER_REQUEST_INTERVAL_SECONDS)
        return response


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


_RACE_ID_RE = re.compile(r"race_id=(\d{12})")
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*発走")
_WEIGHT_RE = re.compile(r"^\d{2}\.\d$")
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


def _find_race_ids(target_date: date) -> list[str]:
    """指定日に開催されるレースのrace_id一覧を取得する。"""
    response = _get(
        f"{BASE_URL}/top/race_list_sub.html",
        params={"kaisai_date": target_date.strftime("%Y%m%d")},
    )
    race_ids = set()
    for match in _RACE_ID_RE.finditer(response.text):
        race_id = match.group(1)
        if race_id[:4] != str(target_date.year):
            continue
        if race_id[4:6] not in VENUE_CODES:
            continue
        race_ids.add(race_id)
    return sorted(race_ids)


def _fetch_jra_odds(race_id: str, odds_type: str) -> dict:
    try:
        response = _get(
            f"{BASE_URL}/api/api_get_jra_odds.html",
            params={"race_id": race_id, "type": odds_type},
        )
        odds_data = response.json()["data"]["odds"][odds_type]
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        logger.warning("failed to fetch odds for race_id=%s type=%s: %s", race_id, odds_type, exc)
        return {}
    return odds_data if isinstance(odds_data, dict) else {}


def _fetch_win_odds(race_id: str) -> dict[str, float]:
    """単勝オッズを馬番(2桁文字列)->オッズのdictで返す。取得失敗時は空dict。"""
    odds_data = _fetch_jra_odds(race_id, JRA_ODDS_TYPES[BET_TYPE_WIN])

    result: dict[str, float] = {}
    for horse_number, values in odds_data.items():
        odds = _parse_odds_value(values)
        if odds is not None:
            result[horse_number] = odds
    return result


def normalize_combination(numbers) -> str:
    """馬番の集合を昇順の買い目文字列にする(例 [9, 4] -> '4-9')。

    馬連オッズのキー・払戻の買い目・Bet.combination で同じ表記を使い、突き合わせ可能にする。
    """
    return "-".join(str(n) for n in sorted(int(x) for x in numbers))


def _fetch_single_horse_odds(race_id: str, bet_type: str) -> dict[str, float]:
    odds_type = JRA_ODDS_TYPES[bet_type]
    odds_data = _fetch_jra_odds(race_id, odds_type)
    result: dict[str, float] = {}
    for horse_number, values in odds_data.items():
        if not str(horse_number).isdigit():
            continue
        odds = _parse_odds_value(values)
        if odds is not None:
            result[str(int(horse_number))] = odds
    return result


def _fetch_pair_odds(race_id: str, bet_type: str) -> dict[str, float]:
    odds_type = JRA_ODDS_TYPES[bet_type]
    odds_data = _fetch_jra_odds(race_id, odds_type)
    result: dict[str, float] = {}
    for pair_key, values in odds_data.items():
        if len(pair_key) != 4 or not pair_key.isdigit():
            continue
        odds = _parse_odds_value(values)
        if odds is not None:
            result[normalize_combination((pair_key[:2], pair_key[2:]))] = odds
    return result


def fetch_quinella_odds(race_id: str) -> dict[str, float]:
    """馬連オッズを 買い目('4-9') -> オッズ のdictで返す。取得失敗時は空dict。"""
    return _fetch_pair_odds(race_id, BET_TYPE_QUINELLA)


def fetch_bet_type_odds(race_id: str, bet_type: str) -> dict[str, float]:
    """券種別オッズを共通形式で返す。単勝/複勝は馬番、馬連/ワイドは組み合わせ。"""
    if bet_type in (BET_TYPE_WIN, BET_TYPE_PLACE):
        return _fetch_single_horse_odds(race_id, bet_type)
    if bet_type in (BET_TYPE_QUINELLA, BET_TYPE_WIDE):
        return _fetch_pair_odds(race_id, bet_type)
    raise ValueError(f"unsupported bet_type: {bet_type}")


def fetch_supported_odds(race_id: str) -> dict[str, dict[str, float]]:
    """買い目判定で使う主要券種のオッズをまとめて取得する。"""
    return {
        bet_type: fetch_bet_type_odds(race_id, bet_type)
        for bet_type in (BET_TYPE_WIN, BET_TYPE_PLACE, BET_TYPE_QUINELLA, BET_TYPE_WIDE)
    }


def _fill_popularity(entries: list[dict]) -> None:
    """人気(予想人気)が取得できていない馬を、オッズの昇順から導出して補完する。

    netkeiba側で人気が取れていればそれを優先し、取れていない馬だけ
    オッズの低い順(=1番人気)で順位を割り当てる。
    """
    if all(e.get("popularity") for e in entries):
        return
    with_odds = sorted(
        (e for e in entries if e.get("odds") is not None and e["odds"] > 0),
        key=lambda e: e["odds"],
    )
    for rank, entry in enumerate(with_odds, start=1):
        if not entry.get("popularity"):
            entry["popularity"] = rank


def _parse_row_odds(row) -> tuple[float | None, int | None]:
    """出馬表の行から予想オッズと予想人気を取り出す(未確定ならNone)。

    netkeibaの出馬表ではオッズ/人気のセルに ``<span id="odds-1_07">5.4</span>`` /
    ``<span id="ninki-1_07">3</span>`` のIDが付与されている。発走前で値が未確定の
    場合は "---.-" 等のプレースホルダが入るため、数値にならないものはNoneとする。
    """
    odds: float | None = None
    popularity: int | None = None

    odds_span = row.find("span", id=re.compile(r"^odds-"))
    if odds_span is not None:
        odds = _parse_float(odds_span.get_text(strip=True))

    ninki_span = row.find("span", id=re.compile(r"^ninki-"))
    if ninki_span is not None:
        text = ninki_span.get_text(strip=True)
        if text.isdigit():
            popularity = int(text)

    return odds, popularity


def _needs_rendered_odds(entries: list[dict]) -> bool:
    return any(e.get("odds") is None for e in entries)


def _parse_rendered_odds_values(values) -> dict[int, dict[str, float | int]]:
    rendered: dict[int, dict[str, float | int]] = {}
    if not isinstance(values, dict):
        return {}
    for horse_number_text, row in values.items():
        if not isinstance(row, dict):
            continue
        try:
            horse_number = int(horse_number_text)
        except (TypeError, ValueError):
            continue
        item: dict[str, float | int] = {}
        odds = _parse_float(str(row.get("odds", "")))
        if odds is not None:
            item["odds"] = odds
        popularity_text = str(row.get("popularity", "")).strip()
        if popularity_text.isdigit():
            item["popularity"] = int(popularity_text)
        if item:
            rendered[horse_number] = item
    return rendered


def _read_rendered_win_odds(page, race_id: str) -> dict[int, dict[str, float | int]]:
    """Read pre-race odds/popularity from an already-open Playwright page."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    url = f"{BASE_URL}/race/shutuba.html?race_id={race_id}"
    page.goto(url, wait_until="domcontentloaded", timeout=20000)
    try:
        page.wait_for_function(
            """() => Array.from(document.querySelectorAll('span[id^="odds-"], span[id^="ninki-"]'))
                .some((el) => /^\\d+(?:\\.\\d+)?$/.test((el.textContent || '').trim()))""",
            timeout=5000,
        )
    except PlaywrightTimeoutError:
        logger.info("rendered odds did not appear before timeout: race_id=%s", race_id)

    values = page.evaluate(
        """() => {
            const byHorse = {};
            const put = (id, key, raw) => {
                const match = /_(\\d+)$/.exec(id || '');
                if (!match) return;
                const horseNumber = Number(match[1]);
                const text = (raw || '').trim();
                if (!byHorse[horseNumber]) byHorse[horseNumber] = {};
                byHorse[horseNumber][key] = text;
            };
            document.querySelectorAll('span[id^="odds-"]').forEach((el) => {
                put(el.id, 'odds', el.textContent);
            });
            document.querySelectorAll('span[id^="ninki-"]').forEach((el) => {
                put(el.id, 'popularity', el.textContent);
            });
            return byHorse;
        }"""
    )
    return _parse_rendered_odds_values(values)


def _new_rendered_page(browser):
    page = browser.new_page(user_agent=USER_AGENT, locale="ja-JP", timezone_id="Asia/Tokyo")
    page.route(
        "**/*",
        lambda route: route.abort()
        if route.request.resource_type in {"image", "stylesheet", "font"}
        else route.continue_(),
    )
    return page


class RenderedOddsClient:
    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._page = None

    def open(self) -> None:
        if self._page is not None:
            return
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        _metrics.playwright_browser_starts += 1
        self._page = _new_rendered_page(self._browser)

    def fetch_win_odds(self, race_id: str) -> dict[int, dict[str, float | int]]:
        self.open()
        _metrics.playwright_pages += 1
        try:
            return _read_rendered_win_odds(self._page, race_id)
        finally:
            time.sleep(settings.SCRAPER_REQUEST_INTERVAL_SECONDS)

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
        self._page = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def _fetch_rendered_win_odds(race_id: str) -> dict[int, dict[str, float | int]]:
    """Use a real browser to read pre-race odds/popularity after netkeiba JS updates the page.

    The normal requests path is cheaper and remains the default. This function is a fallback for
    races where the static HTML/API does not expose values that are visible in a browser.
    """
    try:
        with RenderedOddsClient() as client:
            return client.fetch_win_odds(race_id)
    except ImportError:
        logger.warning("playwright is not installed; skip rendered odds for race_id=%s", race_id)
        return {}
    except Exception as exc:
        logger.warning("failed to fetch rendered odds for race_id=%s: %s", race_id, exc)
        return {}


def _parse_entry_rows(soup: BeautifulSoup) -> list[dict]:
    """出馬表テーブルの各行から馬番・馬名・騎手(名/ID)・斤量・予想オッズ・予想人気を抽出する。

    netkeibaのクラス名("Umaban*"/"Waku*")が付与されている前提だが、
    付与されていない場合は値の範囲から馬番(1-18)・斤量(45.0-65.0)を推測する。
    """
    entries: list[dict] = []
    seen_horse_numbers: set[int] = set()

    for row in soup.find_all("tr"):
        horse_link = row.find("a", href=re.compile(r"/horse/\w+"))
        if horse_link is None:
            continue
        jockey_link = row.find("a", href=re.compile(r"/jockey/"))

        horse_number: int | None = None
        weight: float | None = None
        for cell in row.find_all("td"):
            classes = " ".join(cell.get("class", []))
            text = cell.get_text(strip=True)

            if horse_number is None and "Umaban" in classes and text.isdigit():
                horse_number = int(text)
                continue
            if "Waku" in classes:
                continue
            if horse_number is None and re.fullmatch(r"\d{1,2}", text):
                num = int(text)
                if 1 <= num <= 18:
                    horse_number = num
                    continue
            if weight is None and _WEIGHT_RE.match(text):
                w = float(text)
                if 45.0 <= w <= 65.0:
                    weight = w

        if horse_number is None:
            # 出馬表以外のウィジェット(関連馬の一覧など)にも /horse/ リンクを含む行が
            # 存在するため、馬番が判定できない行はデバッグログのみで読み飛ばす
            logger.debug("could not determine horse_number, skip row")
            continue
        if horse_number in seen_horse_numbers:
            continue
        seen_horse_numbers.add(horse_number)

        # 性齢・厩舎(調教師)・馬体重はnetkeibaのクラス名(Barei/Trainer/Weight)で特定する
        barei_cell = row.find("td", class_="Barei")
        sex, age = _parse_sex_age(barei_cell.get_text(strip=True)) if barei_cell else (None, None)
        if sex is None:
            # 除外・取消馬は性齢セルにクラスが付かないことがあるため、性齢書式のセルを探す
            for cell in row.find_all("td"):
                text = cell.get_text(strip=True)
                if re.fullmatch(r"[牡牝セせ]\s*\d{1,2}", text):
                    sex, age = _parse_sex_age(text)
                    break

        trainer_cell = row.find("td", class_="Trainer")
        trainer_link = (
            trainer_cell.find("a", href=re.compile(r"/trainer/")) if trainer_cell else None
        )
        # 厩舎名はリンクテキスト(調教師名)を優先し、無ければセル全文(トレセン区分+名)
        trainer = (
            trainer_link.get_text(strip=True)
            if trainer_link
            else (trainer_cell.get_text(strip=True) if trainer_cell else "")
        )
        trainer_id = _parse_trainer_id(trainer_link.get("href")) if trainer_link else None

        weight_cell = row.find("td", class_="Weight")
        horse_weight, horse_weight_diff = (
            _parse_horse_weight(weight_cell.get_text(strip=True)) if weight_cell else (None, None)
        )

        odds, popularity = _parse_row_odds(row)
        entries.append(
            {
                "horse_number": horse_number,
                "horse_id": _parse_horse_id(horse_link.get("href")),
                "horse_name": horse_link.get_text(strip=True),
                "sex": sex,
                "age": age,
                "jockey": jockey_link.get_text(strip=True) if jockey_link else "",
                "jockey_id": _parse_jockey_id(jockey_link.get("href") if jockey_link else None),
                "trainer": trainer or None,
                "trainer_id": trainer_id,
                "weight": weight,
                "horse_weight": horse_weight,
                "horse_weight_diff": horse_weight_diff,
                "odds": odds,
                "popularity": popularity,
            }
        )

    return entries


def _parse_race_name(soup: BeautifulSoup) -> str:
    el = soup.find(class_="RaceName")
    if el is None:
        return ""
    return _TIME_RE.sub("", el.get_text(" ", strip=True)).strip()


def _parse_start_time(soup: BeautifulSoup, target_date: date) -> datetime | None:
    match = _TIME_RE.search(soup.get_text("\n"))
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    return datetime.combine(target_date, dt_time(hour=hour, minute=minute))


# 出馬表ヘッダ(RaceData01: "芝1600m (右) / 天候:晴 / 馬場:良" 等)からレース条件を取り出す正規表現
_TRACK_DISTANCE_RE = re.compile(r"([芝ダ障])\s*(\d{3,4})\s*m")
_DIRECTION_RE = re.compile(r"\(\s*(右|左|直)")
# netkeibaの出馬表ヘッダは馬場を1文字に略す(良/稍/重/不)ため1文字で拾い正式名へ変換する
_GOING_RE = re.compile(r"馬場\s*[:：]?\s*(良|稍|重|不)")
_GOING_MAP = {"良": "良", "稍": "稍重", "重": "重", "不": "不良"}
_WEATHER_RE = re.compile(r"天候\s*[:：]?\s*(晴|曇|小雨|雨|小雪|雪)")
_CLASS_KEYWORDS = ("新馬", "未勝利", "1勝クラス", "2勝クラス", "3勝クラス", "オープン")
_GRADE_MAP = {"1": "G1", "2": "G2", "3": "G3"}


def _parse_race_conditions(soup: BeautifulSoup) -> dict:
    """出馬表ヘッダから距離・コース・馬場・天候・クラスを取り出す。

    取得できない項目はNone(数日先のレースは馬場・天候が未定)。HTML構造の変化に
    備え、特定のクラス名に依存しすぎず本文テキストの正規表現で拾う。
    """
    header = soup.find(class_="RaceData01")
    text = header.get_text(" ", strip=True) if header is not None else soup.get_text(" ", strip=True)

    track_type = distance = direction = going = weather = None
    m = _TRACK_DISTANCE_RE.search(text)
    if m:
        track_type = _TRACK_TYPE_MAP.get(m.group(1))
        distance = int(m.group(2))
    m = _DIRECTION_RE.search(text)
    if m:
        direction = m.group(1)
    m = _GOING_RE.search(text)
    if m:
        going = _GOING_MAP.get(m.group(1))
    m = _WEATHER_RE.search(text)
    if m:
        weather = m.group(1)

    return {
        "distance": distance,
        "track_type": track_type,
        "direction": direction,
        "going": going,
        "weather": weather,
        "race_class": _parse_race_class(soup),
    }


def _parse_race_class(soup: BeautifulSoup) -> str | None:
    """格(G1/G2/G3)と条件(新馬/未勝利/n勝クラス/オープン)を組み立てて返す。

    グレードアイコンはページ内のナビ等にも存在する(全ページにG1アイコンがある)ため、
    レース名要素(RaceName)の内側にあるアイコンだけを見る。G1/G2/G3以外
    (リステッド等のType5/13など)は格なし扱いとする。
    """
    grade = None
    race_name_el = soup.find(class_="RaceName")
    if race_name_el is not None:
        icon = race_name_el.find("span", class_=re.compile(r"Icon_GradeType\d"))
        if icon is not None:
            for cls in icon.get("class", []):
                match = re.match(r"Icon_GradeType(\d+)$", cls)
                if match:
                    grade = _GRADE_MAP.get(match.group(1))
                    break

    data02 = soup.find(class_="RaceData02")
    # netkeibaは全角数字("１勝クラス")で書くため、NFKC正規化して半角キーワードと突き合わせる
    condition_text = (
        unicodedata.normalize("NFKC", data02.get_text(" ", strip=True)) if data02 is not None else ""
    )
    condition = next((kw for kw in _CLASS_KEYWORDS if kw in condition_text), None)

    parts = [p for p in (grade, condition) if p]
    return " ".join(parts) if parts else None


def fetch_upcoming_races(target_date: date, include_started: bool = False) -> list[dict]:
    """指定日に開催されるレースの出走馬・オッズ情報を取得する。

    戻り値は races / entries テーブルへ保存できる形式の辞書のリストとする。
    発走時刻が現在時刻を過ぎているレースは除外する
    (``include_started=True`` の場合は除外しない。過去レースのバックフィル用。
    過去レースでもオッズAPIは最終オッズを返す)。
    """
    metrics_start = _metrics_snapshot()
    started_at = time.perf_counter()
    now = now_jst()
    races: list[dict] = []
    rendered_client: RenderedOddsClient | None = None

    try:
        for race_id in _find_race_ids(target_date):
            try:
                response = _get(f"{BASE_URL}/race/shutuba.html", params={"race_id": race_id})
                soup = BeautifulSoup(response.text, "html.parser")

                entries = _parse_entry_rows(soup)
                if not entries:
                    logger.warning("no entries parsed for race_id=%s, skip", race_id)
                    continue

                start_time = _parse_start_time(soup, target_date)
                if not include_started and start_time is not None and start_time <= now:
                    continue

                # オッズは2系統: JRAオッズAPI(発走当日に確定値が出る)を最優先とし、
                # まだ確定オッズが無い未確定レースでは出馬表の予想オッズ(_parse_row_oddsで
                # 取得済み)を残す。人気だけ欠ける場合はオッズ順で補完し、ブラウザ描画は
                # オッズ自体が欠けている時だけ使う。
                odds_map = _fetch_win_odds(race_id)
                for entry in entries:
                    api_odds = odds_map.get(f"{entry['horse_number']:02d}")
                    if api_odds is not None:
                        entry["odds"] = api_odds
                _fill_popularity(entries)

                if _needs_rendered_odds(entries):
                    rendered_odds = {}
                    try:
                        if rendered_client is None:
                            rendered_client = RenderedOddsClient()
                        rendered_odds = rendered_client.fetch_win_odds(race_id)
                    except ImportError:
                        logger.warning("playwright is not installed; skip rendered odds for race_id=%s", race_id)
                    except Exception as exc:
                        logger.warning("failed to fetch rendered odds for race_id=%s: %s", race_id, exc)
                    for entry in entries:
                        rendered_entry = rendered_odds.get(entry["horse_number"])
                        if not rendered_entry:
                            continue
                        if rendered_entry.get("odds") is not None:
                            entry["odds"] = rendered_entry["odds"]
                        if rendered_entry.get("popularity") is not None:
                            entry["popularity"] = rendered_entry["popularity"]
                    _fill_popularity(entries)

                info = parse_race_key(race_id)
                races.append(
                    {
                        "race_key": race_id,
                        "race_date": target_date,
                        "venue": info["venue"],
                        "race_number": info["race_number"],
                        "race_name": _parse_race_name(soup),
                        "start_time": start_time,
                        "entries": entries,
                        **_parse_race_conditions(soup),
                    }
                )
            except requests.RequestException as exc:
                logger.warning("failed to fetch race_id=%s: %s", race_id, exc)
                continue
    finally:
        if rendered_client is not None:
            rendered_client.close()
        _log_metrics(
            f"fetch_upcoming_races date={target_date} include_started={include_started} races={len(races)}",
            started_at,
            metrics_start,
        )

    return races


_BET_TYPE_MAP = {"単勝": "win", "複勝": "place", "馬連": "quinella", "ワイド": "wide"}
# 1頭指定の券種(払戻が馬番1つ)。それ以外(馬連等)は買い目(複数馬番)として扱う
_SINGLE_BET_TYPES = {"win", "place"}


def _parse_result_entries(soup: BeautifulSoup) -> list[dict]:
    for table in soup.find_all("table"):
        header_cells = [c.get_text(strip=True) for c in table.find_all("th")]
        if "着順" in header_cells and "馬番" in header_cells:
            break
    else:
        logger.warning("result table not found")
        return []

    header_row = table.find("tr")
    headers = [c.get_text(strip=True) for c in header_row.find_all(["th", "td"])]
    try:
        rank_idx = headers.index("着順")
        umaban_idx = headers.index("馬番")
    except ValueError:
        logger.warning("expected columns not found in result table: %s", headers)
        return []

    entries: list[dict] = []
    for row in table.find_all("tr")[1:]:
        cells = [c.get_text(strip=True) for c in row.find_all("td")]
        if len(cells) <= max(rank_idx, umaban_idx):
            continue
        if not cells[umaban_idx].isdigit():
            continue
        try:
            finish_position = int(cells[rank_idx])
        except ValueError:
            # 競走中止・取消・除外などは着順が数値にならないため除外する
            continue
        entries.append({"horse_number": int(cells[umaban_idx]), "finish_position": finish_position})

    return entries


def _parse_payouts(soup: BeautifulSoup) -> dict[str, list[dict]]:
    """払戻表をパースする。

    単勝・複勝(1頭): ``{"horse_number": int, "amount": int}`` のリスト。
    馬連等(買い目): ``{"combination": "4-9", "amount": int}`` のリスト。
    """
    payouts: dict[str, list[dict]] = {}
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            th = row.find("th")
            if th is None:
                continue
            bet_type = _BET_TYPE_MAP.get(th.get_text(strip=True))
            if bet_type is None:
                continue

            tds = row.find_all("td")
            if len(tds) < 2:
                continue

            if bet_type in _SINGLE_BET_TYPES:
                numbers = [t.strip() for t in tds[0].get_text("\n").split("\n") if t.strip()]
                amounts = [t.strip() for t in tds[1].get_text("\n").split("\n") if t.strip()]
                for num_text, amount_text in zip(numbers, amounts):
                    amount_text = amount_text.replace(",", "").replace("円", "")
                    if not num_text.isdigit() or not amount_text.isdigit():
                        continue
                    payouts.setdefault(bet_type, []).append(
                        {"horse_number": int(num_text), "amount": int(amount_text)}
                    )
            else:
                # 馬連等は馬番が "4 - 9" のように1セルに入る。先頭2つの馬番を買い目とする
                nums = re.findall(r"\d+", tds[0].get_text(" "))
                amts = re.findall(r"\d+", tds[1].get_text(" ").replace(",", ""))
                if len(nums) >= 2 and amts:
                    payouts.setdefault(bet_type, []).append(
                        {"combination": normalize_combination(nums[:2]), "amount": int(amts[0])}
                    )

    return payouts


def fetch_race_results(race_key: str) -> dict:
    """確定したレースの着順・払い戻し情報を取得する。

    戻り値:
        {
            "race_key": race_key,
            "entries": [{"horse_number": int, "finish_position": int}, ...],
            "payouts": {"win": [{"horse_number": int, "amount": int}, ...],
                        "place": [...]},
        }
    """
    response = _get(f"{BASE_URL}/race/result.html", params={"race_id": race_key})
    soup = BeautifulSoup(response.text, "html.parser")

    return {
        "race_key": race_key,
        "entries": _parse_result_entries(soup),
        "payouts": _parse_payouts(soup),
    }


# --- 馬の過去成績(db.netkeiba.com 馬ページ) -------------------------------

_DISTANCE_RE = re.compile(r"([芝ダ障])\s*(\d{3,4})")
_TRACK_TYPE_MAP = {"芝": "芝", "ダ": "ダート", "障": "障害"}
# レースキーは12桁のnetkeiba race_id。/race/2024/ のような年次リンクを拾わないよう桁数を固定する
_RACE_KEY_RE = re.compile(r"/race/(\d{12})")


def _parse_int_cell(text: str) -> int | None:
    """セル文字列の先頭にある整数を取り出す('480(+4)'→480、'3人'→3 等)。"""
    match = re.search(r"-?\d+", text.replace(",", ""))
    return int(match.group()) if match else None


def _parse_float_cell(text: str) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    return float(match.group()) if match else None


def _parse_distance(text: str) -> tuple[str | None, int | None]:
    """距離セル('芝1600'/'ダ1200'/'障3200')から馬場種別と距離(m)を取り出す。"""
    match = _DISTANCE_RE.search(text.replace(" ", ""))
    if not match:
        return None, None
    return _TRACK_TYPE_MAP.get(match.group(1)), int(match.group(2))


def _parse_time_seconds(text: str) -> float | None:
    """走破タイム('1:33.4'や'33.4')を秒に換算する。"""
    text = text.strip()
    match = re.match(r"(?:(\d+):)?(\d+(?:\.\d+)?)$", text)
    if not match:
        return None
    minutes = int(match.group(1)) if match.group(1) else 0
    return minutes * 60 + float(match.group(2))


def _parse_date_cell(text: str) -> date | None:
    text = text.strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _find_horse_results_table(soup: BeautifulSoup):
    """馬ページの成績表(着順・日付を含むテーブル)を探す。"""
    for table in soup.find_all("table"):
        header_cells = [c.get_text(strip=True) for c in table.find_all("th")]
        if "着順" in header_cells and "日付" in header_cells:
            return table
    return None


def _parse_horse_result_row(headers: list[str], cells) -> dict:
    """成績表1行を辞書化する。着順が数値にならない行(中止・除外等)はfinish_position=None。"""

    def cell(name: str) -> str:
        if name not in headers:
            return ""
        idx = headers.index(name)
        return cells[idx].get_text(strip=True) if idx < len(cells) else ""

    finish_text = cell("着順")
    finish_position = int(finish_text) if finish_text.isdigit() else None

    track_type, distance = _parse_distance(cell("距離"))

    # レースキー・騎手IDはリンクのhrefから取得する
    race_key = None
    jockey_id = None
    if "レース名" in headers:
        link = cells[headers.index("レース名")].find("a") if headers.index("レース名") < len(cells) else None
        if link is not None:
            m = _RACE_KEY_RE.search(link.get("href", ""))
            race_key = m.group(1) if m else None
    if "騎手" in headers and headers.index("騎手") < len(cells):
        link = cells[headers.index("騎手")].find("a")
        if link is not None:
            jockey_id = _parse_jockey_id(link.get("href"))

    return {
        "race_key": race_key,
        "race_date": _parse_date_cell(cell("日付")),
        "venue": cell("開催") or None,
        "race_name": cell("レース名") or None,
        "field_size": _parse_int_cell(cell("頭数")),
        "post_position": _parse_int_cell(cell("枠番")),
        "horse_number": _parse_int_cell(cell("馬番")),
        "odds": _parse_float_cell(cell("オッズ")),
        "popularity": _parse_int_cell(cell("人気")),
        "finish_position": finish_position,
        "jockey": cell("騎手") or None,
        "jockey_id": jockey_id,
        "weight": _parse_float_cell(cell("斤量")),
        "distance": distance,
        "track_type": track_type,
        "going": cell("馬場") or None,
        "time_seconds": _parse_time_seconds(cell("タイム")),
        "margin": cell("着差") or None,
        "passing": cell("通過") or None,
        "last_3f": _parse_float_cell(cell("上り")),
        "horse_weight": _parse_int_cell(cell("馬体重")),
    }


def fetch_horse_results(horse_id: str) -> dict:
    """馬の過去成績を db.netkeiba.com の馬ページから取得する。

    戻り値: ``{"horse_id": str, "name": str | None, "results": [ {成績1行}, ... ]}``。
    過去走が無い(新馬)場合は results が空リスト。HTML構造が想定と異なる場合も
    空リストを返し、例外で収集全体を止めない。

    成績表は馬ページ本体(/horse/{id}/)ではなく成績ページ(/horse/result/{id}/)に
    あるため、そちらを取得する。
    """
    response = _get(f"{DB_BASE_URL}/horse/result/{horse_id}/")
    soup = BeautifulSoup(response.text, "html.parser")

    name_el = soup.find(class_="horse_title")
    name = None
    if name_el is not None:
        h1 = name_el.find("h1")
        if h1 is not None:
            name = h1.get_text(strip=True)

    table = _find_horse_results_table(soup)
    results: list[dict] = []
    if table is not None:
        header_row = table.find("tr")
        headers = [c.get_text(strip=True) for c in header_row.find_all(["th", "td"])]
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if not cells:
                continue
            parsed = _parse_horse_result_row(headers, cells)
            if parsed is not None:
                results.append(parsed)
    else:
        logger.warning("horse results table not found for horse_id=%s", horse_id)

    return {"horse_id": horse_id, "name": name, "results": results}


# 血統表(/horse/ped/{id}/)の馬IDリンク。ped/sire等の短い語ではなく10桁前後のIDだけを拾う
_PED_HORSE_ID_RE = re.compile(r"/horse/([0-9a-z]{8,})/")


def fetch_horse_pedigree_full(horse_id: str, max_generation: int = 5) -> dict:
    """馬の血統を最大5代血統表まで取得する。

    血統表(``table.blood_table``)は各先祖セルの ``rowspan`` で世代を表す
    (5代なら gen1=16, gen2=8, gen3=4, gen4=2, gen5=1)。``gen = total_gens - log2(rowspan)``
    で世代を求め、同一 rowspan のセルは文書順=上→下=正準順なので、その並びを ``position``
    (世代内 0..2^gen-1, 父系先)とする。戻り値の ``ancestors`` は
    ``[{generation, position, horse_id, name}, ...]``。``sire_id``/``sire_name`` は gen1/pos0
    (後方互換: Horse.sire_id 用)。
    """
    response = _get(f"{DB_BASE_URL}/horse/ped/{horse_id}/")
    soup = BeautifulSoup(response.text, "html.parser")
    table = soup.find("table", class_=re.compile("blood_table"))
    ancestors: list[dict] = []
    sire_id: str | None = None
    sire_name: str | None = None
    if table is not None:
        cells = table.find_all("td")
        rowspans: list[int] = []
        for td in cells:
            try:
                rowspans.append(max(1, int(td.get("rowspan", 1))))
            except (TypeError, ValueError):
                rowspans.append(1)
        max_rs = max(rowspans) if rowspans else 1
        total_gens = int(round(math.log2(max_rs))) + 1
        pos_counter: dict[int, int] = {}
        for td, rs in zip(cells, rowspans):
            generation = total_gens - int(round(math.log2(rs)))
            if generation < 1 or generation > max_generation:
                continue
            link = td.find("a")
            if link is not None:
                match = _PED_HORSE_ID_RE.search(link.get("href", ""))
                ancestor_id = match.group(1) if match else None
                ancestor_name = link.get_text(strip=True) or None
            else:
                ancestor_id = None
                ancestor_name = td.get_text(strip=True) or None
            position = pos_counter.get(generation, 0)
            pos_counter[generation] = position + 1
            ancestors.append(
                {
                    "generation": generation,
                    "position": position,
                    "horse_id": ancestor_id,
                    "name": ancestor_name,
                }
            )
            if generation == 1 and position == 0:
                sire_id, sire_name = ancestor_id, ancestor_name
    if not ancestors:
        logger.warning("pedigree table not parsed for horse_id=%s", horse_id)
    return {"sire_id": sire_id, "sire_name": sire_name, "ancestors": ancestors}
