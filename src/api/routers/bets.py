"""買い目一覧 API(モード別の購入履歴・回収率推移)。"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.orm import selectinload

from src.api.deps import _is_admin_request, require_admin
from src.api.serializers import _bet_stats, _iso
from src.common.db import get_session
from src.common.models import Bet, BetStatus, BettingMode

router = APIRouter()


@router.get("/api/bets")
def list_bets(request: Request, mode: str = BettingMode.SIM.value) -> dict:
    if mode not in (BettingMode.SIM.value, BettingMode.PROD.value):
        raise HTTPException(status_code=400, detail="mode は 'sim' か 'prod' を指定してください")
    if mode == BettingMode.PROD.value and not _is_admin_request(request):
        require_admin(request)

    session = get_session()
    try:
        bets = (
            session.query(Bet)
            .options(selectinload(Bet.race), selectinload(Bet.entry))
            .filter(Bet.mode == mode)
            .order_by(Bet.placed_at.desc())
            .all()
        )

        items = []
        for b in bets:
            items.append(
                {
                    "id": b.id,
                    "race_id": b.race_id,
                    "race_date": _iso(b.race.race_date) if b.race else None,
                    "venue": b.race.venue if b.race else None,
                    "race_number": b.race.race_number if b.race else None,
                    "race_name": b.race.race_name if b.race else None,
                    "horse_number": b.entry.horse_number if b.entry else None,
                    "horse_name": b.entry.horse_name if b.entry else None,
                    "combination": b.combination,
                    "bet_type": b.bet_type,
                    "status": b.status,
                    "amount": b.amount,
                    "odds_at_bet": b.odds_at_bet,
                    "model_version": b.model_version,
                    "payout": b.payout,
                    "is_settled": b.is_settled,
                    "placed_at": _iso(b.placed_at),
                }
            )

        # 決済済みの賭けを時系列に並べた累積の投資額・回収額(回収率の推移グラフ用)
        cumulative = []
        invested = 0.0
        payout = 0.0
        settled = [
            b
            for b in sorted(bets, key=lambda b: b.placed_at or datetime.min)
            if b.is_settled and b.status == BetStatus.PLACED.value
        ]
        for b in settled:
            invested += b.amount
            payout += b.payout or 0
            cumulative.append(
                {
                    "placed_at": _iso(b.placed_at),
                    "invested": invested,
                    "payout": payout,
                    "recovery_rate": (payout / invested * 100) if invested else None,
                }
            )

        return {"stats": _bet_stats(bets), "bets": items, "cumulative": cumulative}
    finally:
        session.close()
