"""JRAオッズAPI(api_get_jra_odds.html)から券種別オッズを取得する。"""

import logging
from concurrent.futures import ThreadPoolExecutor

import requests

from src.collector.scraper._core import (
    BASE_URL,
    BET_TYPE_PLACE,
    BET_TYPE_QUINELLA,
    BET_TYPE_WIDE,
    BET_TYPE_WIN,
    JRA_ODDS_TYPES,
    _get,
    _parse_odds_value,
    normalize_combination,
)

logger = logging.getLogger(__name__)


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
    """買い目判定で使う主要券種のオッズをまとめて取得する。

    券種ごとに独立したAPI呼び出しのため並列に取得する。送出間隔はHTTPクライアント側の
    スロットルで一定に保たれる(レートは1リクエスト/間隔のまま)ので、通信待ちが重なる
    分だけ全体の所要時間が縮む。
    """
    bet_types = (BET_TYPE_WIN, BET_TYPE_PLACE, BET_TYPE_QUINELLA, BET_TYPE_WIDE)
    with ThreadPoolExecutor(max_workers=len(bet_types)) as executor:
        results = executor.map(
            lambda bet_type: (bet_type, fetch_bet_type_odds(race_id, bet_type)), bet_types
        )
        return dict(results)
