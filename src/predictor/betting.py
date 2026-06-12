"""賭け戦略の決定、および本番モードでの購入処理。

`place_bet_production` はIPAT(JRA即時購入, https://www.ipat.jra.go.jp/)への
ブラウザ自動操作(Playwright)を実装している。IPATはログインが必須のページのため、
本実装のセレクタは実機で確認したものではない想定の値である。

`settings.IPAT_DRY_RUN=true`(デフォルト)の間は、購入内容を入力した後の
確認画面まで遷移した時点でログ出力のみ行い、最終購入ボタンは押さない。
実際に購入を行う前に、必ずブラウザの開発者ツールで実際のIPAT画面を確認し、
下記 SELECTORS を実物に合わせて調整すること。
"""

import logging

from src.collector.scraper import parse_race_key
from src.common.config import settings
from src.common.dynamic_config import BettingConfig
from src.common.models import Bet, Prediction, Race

logger = logging.getLogger(__name__)

IPAT_TOP_URL = "https://www.ipat.jra.go.jp/"

SUPPORTED_BET_TYPES = {"単勝"}

# 要実機検証: 各画面の入力欄・ボタンのセレクタ。
# IPATにログインできる環境で開発者ツールを使い、実際の id/name/テキストに置き換えること。
SELECTORS = {
    # ログイン(加入者番号 → 暗証番号/P-ARS番号)
    "subscriber_input": "#InpNo",
    "subscriber_submit": "#entryBtn",
    "pin_input": "#i0",
    "pars_input": "#i1",
    "login_submit": "#loginBtn",
    "menu_normal_vote": "text=通常投票",
    # レース選択(場・回・日・R)
    "venue_select": "#JyoCD",
    "kai_select": "#KaiCD",
    "day_select": "#NichiCD",
    "race_select": "#RaceNo",
    # 式別・馬番・金額の入力
    "bet_type_win": "text=単勝",
    "horse_number_input": "#Umaban1",
    "amount_input": "#Money1",
    "add_to_cart": "text=セット",
    # 確認・購入
    "review_submit": "text=購入内容を確認する",
    "purchase_confirm": "text=購入する",
}


def decide_bets(race: Race, predictions: list[Prediction], config: BettingConfig) -> list[Bet]:
    """予測スコアから賭け対象・賭け式・金額を決定し、Bet エンティティのリストを返す。

    戦略: 予測スコア(1着になる確率)が最も高い馬について、

    - スコアが ``config.score_threshold`` 以上
    - 期待値(スコア × 単勝オッズ)が ``config.min_expected_value`` 以上

    の両方を満たす場合に単勝で ``config.amount`` 円を賭ける。
    スコアだけで賭けるとほぼ常に1番人気を買うことになり、長期回収率は
    控除率相当(約80%)に収束しやすいため、期待値の条件を併用する。
    オッズが取得できていない馬には賭けない(期待値を判断できないため)。
    設定値はWebUIから変更できる(``src/common/dynamic_config.py``)。
    """
    if not predictions:
        return []

    best = max(predictions, key=lambda p: p.score)
    if best.score < config.score_threshold:
        return []

    entry = next((e for e in race.entries if e.id == best.entry_id), None)
    if entry is None or entry.odds is None or entry.odds <= 0:
        logger.info("オッズ未取得のため賭けを見送ります: race_id=%s", race.id)
        return []

    expected_value = best.score * entry.odds
    if expected_value < config.min_expected_value:
        return []

    return [
        Bet(
            race_id=race.id,
            entry_id=best.entry_id,
            mode=config.mode,
            bet_type="単勝",
            amount=config.amount,
            odds_at_bet=entry.odds,
            is_settled=False,
        )
    ]


def place_bet_production(bet: Bet) -> None:
    """本番環境で実際に賭けを購入する(IPAT即時購入へのブラウザ自動操作)。

    ``settings.IPAT_DRY_RUN`` が true(デフォルト)の間は、購入内容の入力まで
    行った上でログ出力のみとし、実際の購入操作(最終確定)は行わない。
    """
    if not (settings.IPAT_SUBSCRIBER_NUMBER and settings.IPAT_PIN and settings.IPAT_PARS_NUMBER):
        raise RuntimeError(
            "IPATの認証情報が設定されていません"
            "(IPAT_SUBSCRIBER_NUMBER / IPAT_PIN / IPAT_PARS_NUMBER)。"
        )

    if bet.bet_type not in SUPPORTED_BET_TYPES:
        raise NotImplementedError(f"未対応の式別です: {bet.bet_type}")

    race_info = parse_race_key(bet.race.race_key)
    horse_number = bet.entry.horse_number

    from playwright.sync_api import sync_playwright  # 利用時のみimport(prod以外はPlaywright不要)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            _login(page)
            _select_race(page, race_info)
            _input_bet(page, bet.bet_type, horse_number, bet.amount)

            if settings.IPAT_DRY_RUN:
                logger.warning(
                    "IPAT_DRY_RUN=true のため購入操作は行いません: "
                    "venue=%s kai=%s day=%s race=%s bet_type=%s horse_number=%s amount=%s",
                    race_info["venue"],
                    race_info["kai"],
                    race_info["day"],
                    race_info["race_number"],
                    bet.bet_type,
                    horse_number,
                    bet.amount,
                )
                return

            page.click(SELECTORS["review_submit"])
            page.click(SELECTORS["purchase_confirm"])
            logger.info(
                "IPAT purchase submitted: race_key=%s bet_type=%s horse_number=%s amount=%s",
                bet.race.race_key,
                bet.bet_type,
                horse_number,
                bet.amount,
            )
        finally:
            browser.close()


def _login(page) -> None:
    """要実機検証: IPATのログインフロー(加入者番号 → 暗証番号/P-ARS番号)。"""
    page.goto(IPAT_TOP_URL)
    page.fill(SELECTORS["subscriber_input"], settings.IPAT_SUBSCRIBER_NUMBER)
    page.click(SELECTORS["subscriber_submit"])
    page.fill(SELECTORS["pin_input"], settings.IPAT_PIN)
    page.fill(SELECTORS["pars_input"], settings.IPAT_PARS_NUMBER)
    page.click(SELECTORS["login_submit"])
    page.click(SELECTORS["menu_normal_vote"])


def _select_race(page, race_info: dict) -> None:
    """要実機検証: 場・回・日・レース番号の選択。"""
    page.select_option(SELECTORS["venue_select"], race_info["venue_code"])
    page.select_option(SELECTORS["kai_select"], str(race_info["kai"]))
    page.select_option(SELECTORS["day_select"], str(race_info["day"]))
    page.select_option(SELECTORS["race_select"], str(race_info["race_number"]))


def _input_bet(page, bet_type: str, horse_number: int, amount: float) -> None:
    """要実機検証: 式別・馬番・金額の入力。金額の入力単位は100円。"""
    page.click(SELECTORS["bet_type_win"])
    page.fill(SELECTORS["horse_number_input"], str(horse_number))
    page.fill(SELECTORS["amount_input"], str(int(amount // 100)))
    page.click(SELECTORS["add_to_cart"])
