"""netkeiba.com からレース情報・オッズ・結果を取得するスクレイパー。

責務ごとにモジュール分割しているが、従来 ``from src.collector import scraper`` で
参照していた公開APIをここで再エクスポートし、呼び出し側を変更せず使えるようにする。

- _core   : HTTPクライアント・定数・低レベルパース補助(共有基盤)
- calendar: 開催カレンダー(fetch_kaisai_dates)
- odds    : 券種別オッズAPI
- rendered: Playwright によるオッズ描画取得(フォールバック)
- races   : レース一覧・出馬表(fetch_upcoming_races)
- results : 確定レースの着順・払戻(fetch_race_results)
- horses  : 馬の過去成績・血統
"""

from src.collector.scraper._core import (
    BASE_URL,
    BET_TYPE_PLACE,
    BET_TYPE_QUINELLA,
    BET_TYPE_WIDE,
    BET_TYPE_WIN,
    DB_BASE_URL,
    JRA_ODDS_TYPES,
    USER_AGENT,
    VENUE_CODES,
    NetkeibaHttpClient,
    ScrapeMetrics,
    normalize_combination,
    parse_race_key,
)
from src.collector.scraper.calendar import fetch_kaisai_dates
from src.collector.scraper.horses import fetch_horse_pedigree_full, fetch_horse_results
from src.collector.scraper.odds import (
    fetch_bet_type_odds,
    fetch_quinella_odds,
    fetch_supported_odds,
)
from src.collector.scraper.races import fetch_upcoming_races
from src.collector.scraper.rendered import RenderedOddsClient
from src.collector.scraper.results import fetch_race_results

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
    "NetkeibaHttpClient",
    "ScrapeMetrics",
    "parse_race_key",
    "normalize_combination",
    "fetch_kaisai_dates",
    "fetch_upcoming_races",
    "fetch_race_results",
    "fetch_horse_results",
    "fetch_horse_pedigree_full",
    "fetch_supported_odds",
    "fetch_bet_type_odds",
    "fetch_quinella_odds",
    "RenderedOddsClient",
]
