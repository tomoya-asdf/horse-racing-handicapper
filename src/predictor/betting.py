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
from dataclasses import dataclass
from itertools import combinations, permutations

from src.common.config import settings
from src.common.dynamic_config import BettingConfig
from src.common.models import Bet, Prediction, Race

logger = logging.getLogger(__name__)

IPAT_TOP_URL = "https://www.ipat.jra.go.jp/"

BET_TYPE_WIN = "単勝"
BET_TYPE_PLACE = "複勝"
BET_TYPE_QUINELLA = "馬連"
BET_TYPE_WIDE = "ワイド"

# IPAT自動購入(本番)が対応する券別。複数券種はsim/バックテストのみで、本番自動購入は未対応
SUPPORTED_BET_TYPES = {BET_TYPE_WIN}
SUPPORTED_STRATEGY_BET_TYPES = (BET_TYPE_WIN, BET_TYPE_PLACE, BET_TYPE_QUINELLA, BET_TYPE_WIDE)
MAX_BETS_PER_RACE = 3

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


def normalize_combination(numbers) -> str:
    return "-".join(str(n) for n in sorted(int(x) for x in numbers))


@dataclass(frozen=True)
class BetCandidate:
    bet_type: str
    entry_id: int
    combination: str
    probability: float
    odds: float
    expected_value: float
    amount: float = 0.0


def decide_bets(
    race: Race,
    predictions: list[Prediction],
    config: BettingConfig,
    quinella_odds: dict[str, float] | None = None,
    odds_by_type: dict[str, dict[str, float]] | None = None,
) -> list[Bet]:
    """予測スコアから賭け対象・券別・金額を決定し、Bet エンティティのリストを返す。

    複数券種を併用する。共通の考え方は「スコアだけで買うと1番人気ばかりになり
    長期回収率が控除率(約80%)に収束するため、期待値(確率×オッズ)を併用する」こと。

    単勝・複勝・馬連・ワイドの候補を作り、期待値が閾値以上の候補へ100円単位で配分する。
    オッズが無い対象には賭けない。設定値はWebUIから変更できる。
    """
    if odds_by_type is None:
        odds_by_type = _odds_from_race(race)
        if quinella_odds:
            odds_by_type[BET_TYPE_QUINELLA] = quinella_odds

    candidates = build_bet_candidates(race, predictions, config, odds_by_type)
    selected = allocate_candidates(candidates, config.amount * MAX_BETS_PER_RACE)
    model_version = next(
        (
            getattr(p, "model_version", None)
            for p in predictions
            if getattr(p, "model_version", None)
        ),
        None,
    )
    return [
        Bet(
            race_id=race.id,
            entry_id=c.entry_id,
            mode=config.mode,
            bet_type=c.bet_type,
            combination=None if c.bet_type in (BET_TYPE_WIN, BET_TYPE_PLACE) else c.combination,
            amount=c.amount or config.amount,
            odds_at_bet=c.odds,
            model_version=model_version,
            is_settled=False,
        )
        for c in selected
    ]


def build_bet_candidates(
    race: Race,
    predictions: list[Prediction],
    config: BettingConfig,
    odds_by_type: dict[str, dict[str, float]] | None = None,
) -> list[BetCandidate]:
    if not predictions:
        return []
    odds_by_type = odds_by_type or _odds_from_race(race)
    probs = _normalized_probabilities(predictions)
    raw_scores = {p.entry_id: float(p.score) for p in predictions}
    entry_map = {e.id: e for e in race.entries}
    candidates: list[BetCandidate] = []

    for entry_id, score in raw_scores.items():
        entry = entry_map.get(entry_id)
        if entry is None or score < config.score_threshold:
            continue
        win_odds = odds_by_type.get(BET_TYPE_WIN, {}).get(str(entry.horse_number))
        if win_odds:
            candidates.append(_candidate(BET_TYPE_WIN, entry_id, str(entry.horse_number), score, win_odds))
        place_odds = odds_by_type.get(BET_TYPE_PLACE, {}).get(str(entry.horse_number))
        if place_odds:
            place_prob = _place_probability(entry_id, probs)
            candidates.append(_candidate(BET_TYPE_PLACE, entry_id, str(entry.horse_number), place_prob, place_odds))

    # 上位候補の選抜は生スコア(較正前)で順序付けし、較正で同値に潰れた馬の
    # 並びが不定にならないようにする(生スコアが無い古い予測は較正後スコアで代替)。
    ranked = sorted(
        [p for p in predictions if p.entry_id in entry_map],
        key=lambda p: p.raw_score if p.raw_score is not None else p.score,
        reverse=True,
    )[:6]
    for p1, p2 in combinations(ranked, 2):
        if max(p1.score, p2.score) < config.score_threshold:
            continue
        e1 = entry_map[p1.entry_id]
        e2 = entry_map[p2.entry_id]
        combination = normalize_combination([e1.horse_number, e2.horse_number])
        quinella_odds = odds_by_type.get(BET_TYPE_QUINELLA, {}).get(combination)
        if quinella_odds:
            prob = _quinella_probability(p1.entry_id, p2.entry_id, probs)
            candidates.append(_candidate(BET_TYPE_QUINELLA, p1.entry_id, combination, prob, quinella_odds))
        wide_odds = odds_by_type.get(BET_TYPE_WIDE, {}).get(combination)
        if wide_odds:
            prob = _wide_probability(p1.entry_id, p2.entry_id, probs)
            candidates.append(_candidate(BET_TYPE_WIDE, p1.entry_id, combination, prob, wide_odds))

    return sorted(
        [c for c in candidates if c.expected_value >= config.min_expected_value],
        key=lambda c: (c.expected_value, c.probability),
        reverse=True,
    )


def allocate_candidates(candidates: list[BetCandidate], bankroll: float) -> list[BetCandidate]:
    """期待値上位の候補へ100円単位で配分する。"""
    units = int(bankroll // 100)
    if units <= 0:
        return []
    selected = candidates[: min(MAX_BETS_PER_RACE, len(candidates), units)]
    if not selected:
        return []
    weights = [max(c.expected_value - 1.0, 0.01) for c in selected]
    total = sum(weights)
    raw_units = [max(1, int(units * w / total)) for w in weights]
    while sum(raw_units) > units:
        idx = max(range(len(raw_units)), key=lambda i: raw_units[i])
        raw_units[idx] -= 1
    idx = 0
    while sum(raw_units) < units:
        raw_units[idx % len(raw_units)] += 1
        idx += 1
    return [
        BetCandidate(
            bet_type=c.bet_type,
            entry_id=c.entry_id,
            combination=c.combination,
            probability=c.probability,
            odds=c.odds,
            expected_value=c.expected_value,
            amount=raw_units[i] * 100,
        )
        for i, c in enumerate(selected)
        if raw_units[i] > 0
    ]


def _candidate(bet_type: str, entry_id: int, combination: str, probability: float, odds: float) -> BetCandidate:
    probability = max(0.0, min(float(probability), 1.0))
    return BetCandidate(
        bet_type=bet_type,
        entry_id=entry_id,
        combination=combination,
        probability=probability,
        odds=float(odds),
        expected_value=probability * float(odds),
    )


def _odds_from_race(race: Race) -> dict[str, dict[str, float]]:
    odds: dict[str, dict[str, float]] = {bet_type: {} for bet_type in SUPPORTED_STRATEGY_BET_TYPES}
    for entry in race.entries:
        win_odds = entry.pre_race_odds if entry.pre_race_odds is not None else entry.odds
        if win_odds is not None and win_odds > 0:
            odds[BET_TYPE_WIN][str(entry.horse_number)] = float(win_odds)
    for row in getattr(race, "odds", []) or []:
        if row.odds is not None and row.odds > 0:
            odds.setdefault(row.bet_type, {})[row.combination] = float(row.odds)
    return odds


def _normalized_probabilities(predictions: list[Prediction]) -> dict[int, float]:
    raw = {p.entry_id: max(float(p.score), 0.0001) for p in predictions if p.score is not None}
    total = sum(raw.values())
    if total <= 0:
        return {}
    return {entry_id: value / total for entry_id, value in raw.items()}


def _ordered_probability(order: tuple[int, ...], probs: dict[int, float]) -> float:
    remaining = 1.0
    result = 1.0
    for entry_id in order:
        p = probs.get(entry_id, 0.0)
        if remaining <= 0 or p <= 0:
            return 0.0
        result *= p / remaining
        remaining -= p
    return result


def _place_probability(entry_id: int, probs: dict[int, float]) -> float:
    ids = list(probs)
    if entry_id not in probs:
        return 0.0
    prob = probs[entry_id]
    others = [i for i in ids if i != entry_id]
    for first in others:
        prob += _ordered_probability((first, entry_id), probs)
    for first, second in permutations(others, 2):
        prob += _ordered_probability((first, second, entry_id), probs)
    return min(prob, 1.0)


def _quinella_probability(a: int, b: int, probs: dict[int, float]) -> float:
    return min(_ordered_probability((a, b), probs) + _ordered_probability((b, a), probs), 1.0)


def _wide_probability(a: int, b: int, probs: dict[int, float]) -> float:
    if len(probs) == 2:
        return 1.0
    others = [i for i in probs if i not in (a, b)]
    prob = 0.0
    for third in others:
        for order in permutations((a, b, third), 3):
            prob += _ordered_probability(order, probs)
    return min(prob, 1.0)


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

    from src.collector.scraper import parse_race_key

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
