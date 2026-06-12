"""netkeiba.com からレース情報・オッズ・結果を取得するスクレイパー。

netkeiba.com の公開ページ(race.netkeiba.com)を対象とする。HTMLの構造は
予告なく変更される可能性があるため、取得できない要素は警告ログを出して
スキップする等、できるだけ防御的にパースしている。サイトへの負荷軽減のため
リクエスト毎に ``settings.SCRAPER_REQUEST_INTERVAL_SECONDS`` 秒のスリープを挟む。
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime
from datetime import time as dt_time

import requests
from bs4 import BeautifulSoup

from src.common.config import settings
from src.common.timeutils import now_jst

logger = logging.getLogger(__name__)

BASE_URL = "https://race.netkeiba.com"

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


def _get(url: str, **kwargs) -> requests.Response:
    response = _session.get(url, timeout=10, **kwargs)
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


def _fetch_win_odds(race_id: str) -> dict[str, float]:
    """単勝オッズを馬番(2桁文字列)->オッズのdictで返す。取得失敗時は空dict。"""
    try:
        response = _get(
            f"{BASE_URL}/api/api_get_jra_odds.html",
            params={"race_id": race_id, "type": "1"},
        )
        odds_data = response.json()["data"]["odds"]["1"]
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        logger.warning("failed to fetch odds for race_id=%s: %s", race_id, exc)
        return {}

    result: dict[str, float] = {}
    for horse_number, values in odds_data.items():
        try:
            result[horse_number] = float(values[0])
        except (TypeError, ValueError, IndexError):
            continue
    return result


def _parse_entry_rows(soup: BeautifulSoup) -> list[dict]:
    """出馬表テーブルの各行から馬番・馬名・騎手・斤量を抽出する。

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

        entries.append(
            {
                "horse_number": horse_number,
                "horse_name": horse_link.get_text(strip=True),
                "jockey": jockey_link.get_text(strip=True) if jockey_link else "",
                "weight": weight,
                "odds": None,
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


def fetch_upcoming_races(target_date: date, include_started: bool = False) -> list[dict]:
    """指定日に開催されるレースの出走馬・オッズ情報を取得する。

    戻り値は races / entries テーブルへ保存できる形式の辞書のリストとする。
    発走時刻が現在時刻を過ぎているレースは除外する
    (``include_started=True`` の場合は除外しない。過去レースのバックフィル用。
    過去レースでもオッズAPIは最終オッズを返す)。
    """
    now = now_jst()
    races: list[dict] = []

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

            odds_map = _fetch_win_odds(race_id)
            for entry in entries:
                entry["odds"] = odds_map.get(f"{entry['horse_number']:02d}")

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
                }
            )
        except requests.RequestException as exc:
            logger.warning("failed to fetch race_id=%s: %s", race_id, exc)
            continue

    return races


_BET_TYPE_MAP = {"単勝": "win", "複勝": "place"}


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
            numbers = [t.strip() for t in tds[0].get_text("\n").split("\n") if t.strip()]
            amounts = [t.strip() for t in tds[1].get_text("\n").split("\n") if t.strip()]

            for num_text, amount_text in zip(numbers, amounts):
                amount_text = amount_text.replace(",", "").replace("円", "")
                if not num_text.isdigit() or not amount_text.isdigit():
                    continue
                payouts.setdefault(bet_type, []).append(
                    {"horse_number": int(num_text), "amount": int(amount_text)}
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
