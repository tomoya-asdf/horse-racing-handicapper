"""未確定の bets に対し、レース結果から payout / is_settled を反映する。"""

import logging

from src.collector.scraper import fetch_race_results
from src.common.db import get_session
from src.common.models import Bet, BetStatus

logger = logging.getLogger(__name__)

# Bet.bet_type -> fetch_race_results()が返すpayoutsの式別キー
PAYOUT_KEYS = {"単勝": "win"}


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
            entry = bet.entry
            if entry is None:
                continue

            if entry.finish_position is None:
                race_has_results = any(
                    e.finish_position is not None for e in bet.race.entries
                )
                if race_has_results:
                    # レース結果は確定しているのに着順が無い → 出走取消・競走除外
                    # とみなし、IPATの返還と同様に賭け金をそのまま戻す。
                    # (競走中止も着順なしのため返還扱いになるが、稀なため許容する)
                    bet.payout = bet.amount
                    bet.is_settled = True
                    settled_count += 1
                    logger.info("出走取消/除外として返還扱いにします: bet_id=%s", bet.id)
                continue  # レース結果未確定

            race_key = bet.race.race_key
            if race_key not in results_cache:
                try:
                    results_cache[race_key] = fetch_race_results(race_key)
                except Exception:
                    logger.exception("failed to fetch results for race_key=%s", race_key)
                    results_cache[race_key] = None

            result = results_cache[race_key]
            if result is None or not result.get("payouts"):
                continue  # 払戻情報を取得できなかった場合は次回再試行

            payout_key = PAYOUT_KEYS.get(bet.bet_type)
            if payout_key is None:
                logger.warning(
                    "未対応の式別のため0円で確定します: bet_id=%s bet_type=%s", bet.id, bet.bet_type
                )
                bet.payout = 0.0
                bet.is_settled = True
                settled_count += 1
                continue

            payout_amount = next(
                (
                    p["amount"]
                    for p in result["payouts"].get(payout_key, [])
                    if p["horse_number"] == entry.horse_number
                ),
                0,
            )
            # payouts の金額は100円あたりの払戻金
            bet.payout = bet.amount / 100 * payout_amount
            bet.is_settled = True
            settled_count += 1

        session.commit()
    finally:
        session.close()
    return settled_count
