"""買い目一覧 API(モード別の購入履歴・回収率推移)。"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.orm import selectinload

from src.api.deps import is_admin_request, require_admin
from src.api.serializers import bet_stats, iso
from src.common.db import session_scope
from src.common.models import Bet, BetStatus, BettingMode

router = APIRouter()


@router.get("/api/bets")
def list_bets(
    request: Request,
    mode: str = BettingMode.SIM.value,
    year: str = "",
    month: str = "",
    model_version: str = "",
) -> dict:
    if mode not in (BettingMode.SIM.value, BettingMode.PROD.value):
        raise HTTPException(status_code=400, detail="mode は 'sim' か 'prod' を指定してください")
    if mode == BettingMode.PROD.value and not is_admin_request(request):
        require_admin(request)

    year_num = int(year) if year.isdigit() else None
    month_num = int(month) if month.isdigit() else None

    with session_scope() as session:
        all_bets = (
            session.query(Bet)
            .options(selectinload(Bet.race), selectinload(Bet.entry))
            .filter(Bet.mode == mode)
            .order_by(Bet.placed_at.desc())
            .all()
        )

        # 絞り込みの選択肢は、現モードの全データ(絞り込み前)から作る。
        years = sorted(
            {str(b.race.race_date.year) for b in all_bets if b.race and b.race.race_date},
            reverse=True,
        )
        models = sorted({b.model_version for b in all_bets if b.model_version})

        def matches(b: Bet) -> bool:
            race_date = b.race.race_date if b.race else None
            if year_num is not None and (race_date is None or race_date.year != year_num):
                return False
            if month_num is not None and (race_date is None or race_date.month != month_num):
                return False
            if model_version and b.model_version != model_version:
                return False
            return True

        bets = [b for b in all_bets if matches(b)]

        items = []
        for b in bets:
            items.append(
                {
                    "id": b.id,
                    "race_id": b.race_id,
                    "race_date": iso(b.race.race_date) if b.race else None,
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
                    "placed_at": iso(b.placed_at),
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
                    "placed_at": iso(b.placed_at),
                    "invested": invested,
                    "payout": payout,
                    "recovery_rate": (payout / invested * 100) if invested else None,
                }
            )

        return {
            "stats": bet_stats(bets),
            "bets": items,
            "cumulative": cumulative,
            "filters": {"years": years, "models": models},
        }
