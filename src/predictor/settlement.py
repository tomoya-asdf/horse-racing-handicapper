"""Settle placed bets from race results and payout tables."""

import logging

from src.collector.scraper import fetch_race_results
from src.common.db import get_session
from src.common.models import Bet, BetStatus

logger = logging.getLogger(__name__)

SINGLE_PAYOUT_KEYS = {"単勝": "win", "複勝": "place"}
COMBINATION_PAYOUT_KEYS = {"馬連": "quinella", "ワイド": "wide"}


def _bet_horse_numbers(bet: Bet) -> list[int]:
    if bet.combination:
        return [int(n) for n in bet.combination.split("-")]
    if bet.entry is not None:
        return [bet.entry.horse_number]
    return []


def _result_positions(result: dict) -> dict[int, int]:
    return {
        entry["horse_number"]: entry["finish_position"]
        for entry in result.get("entries", [])
        if entry.get("horse_number") is not None and entry.get("finish_position") is not None
    }


def _apply_result_positions(bet: Bet, positions: dict[int, int]) -> None:
    race = bet.race
    if race is None:
        return
    for entry in race.entries:
        if entry.finish_position is None and entry.horse_number in positions:
            entry.finish_position = positions[entry.horse_number]


def _settle_with_payouts(bet: Bet, result: dict) -> None:
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
        logger.warning("unsupported bet_type; settling as zero payout: bet_id=%s bet_type=%s", bet.id, bet.bet_type)
        amount = 0

    bet.payout = bet.amount / 100 * amount
    bet.is_settled = True


def settle_pending_races() -> int:
    """Settle unsettled placed bets.

    The previous implementation waited until finish positions were already stored
    in the DB. That left bets stuck when collection had not reflected results yet.
    Settlement now fetches race results itself, fills finish positions, then applies
    the payout table.
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
                continue

            positions = _result_positions(result)
            if not positions:
                continue
            _apply_result_positions(bet, positions)

            # If the selected horse is absent from the official result rows, treat
            # it as a refund for cancellation/exclusion. Parser failures keep
            # positions empty above and are retried later instead.
            if any(h not in positions for h in horses):
                bet.payout = bet.amount
                bet.is_settled = True
                settled_count += 1
                logger.info("settled as refund for excluded horse: bet_id=%s", bet.id)
                continue

            _settle_with_payouts(bet, result)
            settled_count += 1

        session.commit()
    finally:
        session.close()
    return settled_count
