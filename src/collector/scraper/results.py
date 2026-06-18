"""確定レースの着順・払戻の取得(fetch_race_results)。"""

import logging
import re

from bs4 import BeautifulSoup

from src.collector.scraper._core import BASE_URL, _get, normalize_combination

logger = logging.getLogger(__name__)

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
