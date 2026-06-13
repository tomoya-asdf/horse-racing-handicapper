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

from src.collector.scraper import normalize_combination, parse_race_key
from src.common.config import settings
from src.common.dynamic_config import BettingConfig
from src.common.models import Bet, Prediction, Race

logger = logging.getLogger(__name__)

IPAT_TOP_URL = "https://www.ipat.jra.go.jp/"

# IPAT自動購入(本番)が対応する券別。馬連はsim/バックテストのみで、本番自動購入は未対応
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


def decide_bets(
    race: Race,
    predictions: list[Prediction],
    config: BettingConfig,
    quinella_odds: dict[str, float] | None = None,
) -> list[Bet]:
    """予測スコアから賭け対象・券別・金額を決定し、Bet エンティティのリストを返す。

    単勝と馬連を併用する。共通の考え方は「スコアだけで買うと1番人気ばかりになり
    長期回収率が控除率(約80%)に収束するため、期待値(確率×オッズ)を併用する」こと。

    - 単勝: 最有力馬のスコアが ``score_threshold`` 以上、かつ 期待値(スコア×単勝オッズ)が
      ``min_expected_value`` 以上なら単勝を買う。
    - 馬連: ``quinella_odds`` が渡され、AI上位2頭のペアの期待値(ペア的中確率×馬連オッズ)が
      ``min_expected_value`` 以上なら馬連を買う(最有力馬のスコアが ``score_threshold`` 未満の
      弱い予想のレースでは見送る)。

    オッズが無い対象には賭けない。設定値はWebUIから変更できる。
    """
    bets: list[Bet] = []
    win_bet = _decide_win(race, predictions, config)
    if win_bet is not None:
        bets.append(win_bet)
    if quinella_odds:
        quinella_bet = _decide_quinella(race, predictions, config, quinella_odds)
        if quinella_bet is not None:
            bets.append(quinella_bet)
    return bets


def _decide_win(race: Race, predictions: list[Prediction], config: BettingConfig) -> Bet | None:
    if not predictions:
        return None

    best = max(predictions, key=lambda p: p.score)
    if best.score < config.score_threshold:
        return None

    entry = next((e for e in race.entries if e.id == best.entry_id), None)
    if entry is None or entry.odds is None or entry.odds <= 0:
        logger.info("オッズ未取得のため単勝を見送ります: race_id=%s", race.id)
        return None

    if best.score * entry.odds < config.min_expected_value:
        return None

    return Bet(
        race_id=race.id,
        entry_id=best.entry_id,
        mode=config.mode,
        bet_type="単勝",
        amount=config.amount,
        odds_at_bet=entry.odds,
        is_settled=False,
    )


def _decide_quinella(
    race: Race,
    predictions: list[Prediction],
    config: BettingConfig,
    quinella_odds: dict[str, float],
) -> Bet | None:
    if len(predictions) < 2:
        return None

    top2 = sorted(predictions, key=lambda p: p.score, reverse=True)[:2]
    p1, p2 = top2[0].score, top2[1].score
    # 弱い予想(最有力でも閾値未満)のレースは見送る。確率は0<p<1のみ扱う
    if p1 < config.score_threshold or not (0 < p2 <= p1 < 1):
        return None

    entry_map = {e.id: e for e in race.entries}
    e1 = entry_map.get(top2[0].entry_id)
    e2 = entry_map.get(top2[1].entry_id)
    if e1 is None or e2 is None:
        return None

    # Harville近似: 2頭が1-2着(順不同)になる確率
    # P = p1*p2/(1-p1) + p2*p1/(1-p2) = p1*p2*(1/(1-p1)+1/(1-p2))
    pair_prob = p1 * p2 * (1.0 / (1.0 - p1) + 1.0 / (1.0 - p2))

    combination = normalize_combination([e1.horse_number, e2.horse_number])
    odds = quinella_odds.get(combination)
    if odds is None or odds <= 0:
        return None

    if pair_prob * odds < config.min_expected_value:
        return None

    return Bet(
        race_id=race.id,
        entry_id=top2[0].entry_id,
        mode=config.mode,
        bet_type="馬連",
        combination=combination,
        amount=config.amount,
        odds_at_bet=odds,
        is_settled=False,
    )


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
