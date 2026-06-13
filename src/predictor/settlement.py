"""未確定の bets に対し、レース結果から payout / is_settled を反映する。"""

import logging

from src.collector.scraper import fetch_race_results
from src.common.db import get_session
from src.common.models import Bet, BetStatus

logger = logging.getLogger(__name__)

# 1頭券種: Bet.bet_type -> payouts のキー(payoutは馬番で突き合わせる)
SINGLE_PAYOUT_KEYS = {"単勝": "win"}
# 買い目券種: Bet.bet_type -> payouts のキー(payoutは combination で突き合わせる)
COMBINATION_PAYOUT_KEYS = {"馬連": "quinella"}


def _bet_horse_numbers(bet: Bet) -> list[int]:
    """賭けの対象馬番(単勝は1頭、馬連は2頭)を返す。"""
    if bet.combination:
        return [int(n) for n in bet.combination.split("-")]
    if bet.entry is not None:
        return [bet.entry.horse_number]
    return []


def _settle_with_payouts(bet: Bet, result: dict) -> None:
    """払戻情報から payout を確定する(取消返還は呼び出し側で先に処理済み)。"""
    payouts = result["payouts"]
    if bet.bet_type in COMBINATION_PAYOUT_KEYS:
        key = COMBINATION_PAYOUT_KEYS[bet.bet_type]
        amount = next(
            (p["amount"] for p in payouts.get(key, []) if p.get("combination") == bet.combination),
            0,
        )
    elif bet.bet_type in SINGLE_PAYOUT_KEYS:
        key = SINGLE_PAYOUT_KEYS[bet.bet_type]
        horse_number = bet.entry.horse_number if bet.entry else None
        amount = next(
            (p["amount"] for p in payouts.get(key, []) if p.get("horse_number") == horse_number),
            0,
        )
    else:
        logger.warning(
            "未対応の式別のため0円で確定します: bet_id=%s bet_type=%s", bet.id, bet.bet_type
        )
        amount = 0

    # payouts の金額は100円あたりの払戻金
    bet.payout = bet.amount / 100 * amount
    bet.is_settled = True


def settle_pending_races() -> int:
    """未確定の bets に payout / is_settled を反映し、決済できた件数を返す。

    決済対象は購入が成立した賭け(status=placed)のみ。pending(購入結果不明)・
    dry_run(実購入なし)・failed(購入失敗)は対象外とし、回収率の集計を汚さないようにする。
    """
    settled_count = 0
    session = get_session()
    try:
        pending_bets = (
            session.query(Bet)
            .filter(Bet.is_settled.is_(False), Bet.status == BetStatus.PLACED.value)
            .all()
        )
        if not pending_bets:
            return 0

        results_cache: dict[str, dict | None] = {}

        for bet in pending_bets:
            race = bet.race
            if race is None:
                continue

            horses = _bet_horse_numbers(bet)
            if not horses:
                continue  # 対象馬が特定できない(壊れた賭け)

            if not any(e.finish_position is not None for e in race.entries):
                continue  # レース結果未確定 → 次回再試行

            # 対象馬のいずれかが出走取消・競走除外・中止(着順なし)なら、
            # IPATの返還と同様に賭け金をそのまま戻す
            positions = {e.horse_number: e.finish_position for e in race.entries}
            if any(positions.get(h) is None for h in horses):
                bet.payout = bet.amount
                bet.is_settled = True
                settled_count += 1
                logger.info("出走取消/除外として返還扱いにします: bet_id=%s", bet.id)
                continue

            race_key = race.race_key
            if race_key not in results_cache:
                try:
                    results_cache[race_key] = fetch_race_results(race_key)
                except Exception:
                    logger.exception("failed to fetch results for race_key=%s", race_key)
                    results_cache[race_key] = None

            result = results_cache[race_key]
            if result is None or not result.get("payouts"):
                continue  # 払戻情報を取得できなかった場合は次回再試行

            _settle_with_payouts(bet, result)
            settled_count += 1

        session.commit()
    finally:
        session.close()
    return settled_count
