"""馬の過去成績・血統の取得(db.netkeiba.com 馬ページ)。"""

import logging
import math
import re
from datetime import date, datetime

from bs4 import BeautifulSoup

from src.collector.scraper._core import (
    DB_BASE_URL,
    _DISTANCE_RE,
    _TRACK_TYPE_MAP,
    _get,
    _parse_jockey_id,
)

logger = logging.getLogger(__name__)

# レースキーは12桁のnetkeiba race_id。/race/2024/ のような年次リンクを拾わないよう桁数を固定する
_RACE_KEY_RE = re.compile(r"/race/(\d{12})")
# 血統表(/horse/ped/{id}/)の馬IDリンク。ped/sire等の短い語ではなく10桁前後のIDだけを拾う
_PED_HORSE_ID_RE = re.compile(r"/horse/([0-9a-z]{8,})/")


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
