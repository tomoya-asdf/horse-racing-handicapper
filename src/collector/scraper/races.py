"""レース一覧・出馬表・レース条件の取得(fetch_upcoming_races)。"""

import logging
import re
import time
import unicodedata
from datetime import date, datetime
from datetime import time as dt_time

import requests
from bs4 import BeautifulSoup

from src.collector.scraper._core import *  # noqa: F401,F403  (共有基盤を取り込む)
from src.collector.scraper._core import (
    BASE_URL,
    VENUE_CODES,
    _get,
    _log_metrics,
    _metrics_snapshot,
    _parse_float,
    _parse_horse_id,
    _parse_horse_weight,
    _parse_jockey_id,
    _parse_sex_age,
    _parse_trainer_id,
    _RACE_ID_RE,
    _TIME_RE,
    _TRACK_TYPE_MAP,
    _WEIGHT_RE,
    parse_race_key,
)
from src.collector.scraper.odds import _fetch_win_odds
from src.collector.scraper.rendered import RenderedOddsClient
from src.common.timeutils import now_jst

logger = logging.getLogger(__name__)


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


def _build_race_detail(
    race_id: str,
    target_date: date,
    now: datetime,
    include_started: bool,
    rendered_state: dict,
) -> dict | None:
    """1レース分の出馬表・オッズを取得して保存用の辞書を返す(取得不可ならNone)。

    ``rendered_state`` は {"client": RenderedOddsClient|None} で、Playwright
    クライアントを呼び出し側でまとめて使い回す/後始末するための受け皿。
    """
    response = _get(f"{BASE_URL}/race/shutuba.html", params={"race_id": race_id})
    soup = BeautifulSoup(response.text, "html.parser")

    entries = _parse_entry_rows(soup)
    if not entries:
        logger.warning("no entries parsed for race_id=%s, skip", race_id)
        return None

    start_time = _parse_start_time(soup, target_date)
    if not include_started and start_time is not None and start_time <= now:
        return None

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
            if rendered_state.get("client") is None:
                rendered_state["client"] = RenderedOddsClient()
            rendered_odds = rendered_state["client"].fetch_win_odds(race_id)
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
    return {
        "race_key": race_id,
        "race_date": target_date,
        "venue": info["venue"],
        "race_number": info["race_number"],
        "race_name": _parse_race_name(soup),
        "start_time": start_time,
        "entries": entries,
        **_parse_race_conditions(soup),
    }


def fetch_single_race(
    race_id: str, target_date: date, include_started: bool = True
) -> dict | None:
    """単一レースの出馬表・オッズだけを取得する(発走直前の再予測・賭け判定用)。

    1レースの最新化のために ``fetch_upcoming_races`` で全場・全レースを取り直すと
    数十リクエストかかるため、対象レースだけを取りに行く軽量版。既定では発走済み
    でも取得する(直前で発走時刻を跨いでも最新オッズを拾うため)。取得不可ならNone。
    """
    rendered_state: dict = {"client": None}
    try:
        return _build_race_detail(
            race_id, target_date, now_jst(), include_started, rendered_state
        )
    except requests.RequestException as exc:
        logger.warning("failed to fetch race_id=%s: %s", race_id, exc)
        return None
    finally:
        if rendered_state["client"] is not None:
            rendered_state["client"].close()


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
    rendered_state: dict = {"client": None}

    try:
        for race_id in _find_race_ids(target_date):
            try:
                race = _build_race_detail(
                    race_id, target_date, now, include_started, rendered_state
                )
                if race is not None:
                    races.append(race)
            except requests.RequestException as exc:
                logger.warning("failed to fetch race_id=%s: %s", race_id, exc)
                continue
    finally:
        if rendered_state["client"] is not None:
            rendered_state["client"].close()
        _log_metrics(
            f"fetch_upcoming_races date={target_date} include_started={include_started} races={len(races)}",
            started_at,
            metrics_start,
        )

    return races
