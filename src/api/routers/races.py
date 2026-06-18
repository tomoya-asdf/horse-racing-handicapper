"""レース一覧・詳細・開催日カレンダーの API。"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from src.api.deps import _is_admin_request
from src.api.serializers import _iso, _rank_entries
from src.common.db import get_session
from src.common.dynamic_config import load_betting_config
from src.common.models import (
    Bet,
    BettingMode,
    Entry,
    KaisaiDate,
    Race,
    RaceCollectionStatus,
)
from src.common.timeutils import now_jst
from src.predictor import betting

router = APIRouter()


@router.get("/api/races")
def list_races(
    request: Request,
    limit: int = 30,
    offset: int = 0,
    race_name: str | None = None,
    race_date: str | None = None,
    race_number: int | None = None,
    venue: str | None = None,
    status: str | None = None,
    horse_name: str | None = None,
    jockey: str | None = None,
    trainer: str | None = None,
    prediction: str | None = None,
    bet: str | None = None,
) -> dict:
    is_admin = _is_admin_request(request)
    page_limit = min(max(limit, 1), 200)
    page_offset = max(offset, 0)
    session = get_session()
    try:
        venues = [
            row[0]
            for row in session.query(Race.venue)
            .filter(Race.venue.isnot(None))
            .distinct()
            .order_by(Race.venue)
            .all()
        ]
        query = session.query(Race)
        if race_name:
            query = query.filter(Race.race_name.ilike(f"%{race_name.strip()}%"))
        if race_date:
            try:
                parsed_date = datetime.strptime(race_date, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(status_code=400, detail="race_date は YYYY-MM-DD で指定してください")
            query = query.filter(Race.race_date == parsed_date)
        if race_number is not None:
            if race_number < 1 or race_number > 12:
                raise HTTPException(status_code=400, detail="race_number は 1-12 で指定してください")
            query = query.filter(Race.race_number == race_number)
        if venue:
            query = query.filter(Race.venue == venue.strip())
        if status == "finished":
            query = query.filter(Race.entries.any(Entry.finish_position.isnot(None)))
        elif status == "unfinished":
            query = query.filter(
                Race.start_time.isnot(None),
                Race.start_time > now_jst(),
                ~Race.entries.any(Entry.finish_position.isnot(None)),
            )
        elif status == "upcoming":
            query = query.filter(Race.start_time.isnot(None), Race.start_time > now_jst())
        if horse_name:
            query = query.filter(Race.entries.any(Entry.horse_name.ilike(f"%{horse_name.strip()}%")))
        if jockey:
            query = query.filter(Race.entries.any(Entry.jockey.ilike(f"%{jockey.strip()}%")))
        if trainer:
            query = query.filter(Race.entries.any(Entry.trainer.ilike(f"%{trainer.strip()}%")))
        if prediction == "yes":
            query = query.filter(Race.predictions.any())
        elif prediction == "no":
            query = query.filter(~Race.predictions.any())
        if bet == "yes":
            query = query.filter(
                Race.bets.any()
                if is_admin
                else Race.bets.any(Bet.mode == BettingMode.SIM.value)
            )
        elif bet == "no":
            query = query.filter(
                ~Race.bets.any()
                if is_admin
                else ~Race.bets.any(Bet.mode == BettingMode.SIM.value)
            )

        query = query.distinct()
        total = query.with_entities(func.count(func.distinct(Race.id))).scalar() or 0
        races = (
            query.options(
                selectinload(Race.entries),
                selectinload(Race.predictions),
                selectinload(Race.bets),
                selectinload(Race.odds),
            )
            .order_by(Race.race_date.desc(), Race.start_time.desc(), Race.id.desc())
            .offset(page_offset)
            .limit(page_limit)
            .all()
        )

        items = []
        for race in races:
            entry_map = {e.id: e for e in race.entries}
            top = None
            if race.predictions:
                latest = max(race.predictions, key=lambda p: p.created_at or datetime.min)
                candidates = [
                    p for p in race.predictions if p.model_version == latest.model_version
                ]
                best = max(candidates, key=lambda p: p.score)
                best_entry = entry_map.get(best.entry_id)
                top = {
                    "horse_number": best_entry.horse_number if best_entry else None,
                    "horse_id": best_entry.horse_id if best_entry else None,
                    "horse_name": best_entry.horse_name if best_entry else None,
                    "score": best.score,
                    "model_version": best.model_version,
                }
            items.append(
                {
                    "id": race.id,
                    "race_key": race.race_key,
                    "race_date": _iso(race.race_date),
                    "venue": race.venue,
                    "race_number": race.race_number,
                    "race_name": race.race_name,
                    "start_time": _iso(race.start_time),
                    "distance": race.distance,
                    "track_type": race.track_type,
                    "going": race.going,
                    "race_class": race.race_class,
                    "entry_count": len(race.entries),
                    "finished": any(e.finish_position is not None for e in race.entries),
                    "top_prediction": top,
                    "bet_count": len(
                        [b for b in race.bets if is_admin or b.mode != BettingMode.PROD.value]
                    ),
                }
            )
    finally:
        session.close()
    return {
        "races": items,
        "total": total,
        "limit": page_limit,
        "offset": page_offset,
        "venues": venues,
    }


@router.get("/api/race-dates")
def race_dates() -> dict:
    """カレンダー表示用の日付一覧を返す。

    - ``collected``: レースデータが存在する日(収集済み)
    - ``scheduled``: netkeibaの開催カレンダー上の開催日(``kaisai_dates``)

    レース一覧画面のカレンダーで、収集済み(色付け)と「開催予定だが未収集」
    (別色)を区別するために両方返す。
    """
    session = get_session()
    try:
        collected = [
            row[0].isoformat()
            for row in session.query(Race.race_date)
            .filter(Race.race_date.isnot(None))
            .distinct()
            .order_by(Race.race_date)
            .all()
        ]
        scheduled = [
            row[0].isoformat()
            for row in session.query(KaisaiDate.kaisai_date)
            .order_by(KaisaiDate.kaisai_date)
            .all()
        ]
        return {"collected": collected, "scheduled": scheduled}
    finally:
        session.close()


@router.get("/api/races/{race_id}")
def race_detail(request: Request, race_id: int) -> dict:
    is_admin = _is_admin_request(request)
    session = get_session()
    try:
        race = (
            session.query(Race)
            .options(
                selectinload(Race.entries),
                selectinload(Race.predictions),
                selectinload(Race.bets),
                selectinload(Race.odds),
            )
            .filter(Race.id == race_id)
            .one_or_none()
        )
        if race is None:
            raise HTTPException(status_code=404, detail="race not found")

        score_map: dict[int, float] = {}
        # 順位付け用の生スコア(較正前)。等張回帰の較正後スコアは同値に潰れて
        # 同順位が生じるため、順位は生スコアで決める。生スコアが無い古い予測は
        # 較正後スコアで代替する。
        rank_score_map: dict[int, float] = {}
        model_version = None
        if race.predictions:
            latest = max(race.predictions, key=lambda p: p.created_at or datetime.min)
            model_version = latest.model_version
            for p in race.predictions:
                if p.model_version != model_version:
                    continue
                score_map[p.entry_id] = p.score
                rank_score_map[p.entry_id] = p.raw_score if p.raw_score is not None else p.score
        collected_kinds = {
            kind
            for (kind,) in session.query(RaceCollectionStatus.kind).filter(
                RaceCollectionStatus.race_id == race_id
            )
        }
        collection_status = {
            "horse_results": "horse_results" in collected_kinds,
        }
        visible_bets = [b for b in race.bets if is_admin or b.mode != BettingMode.PROD.value]
        bet_entry_ids = {b.entry_id for b in visible_bets}
        betting_config = load_betting_config()
        # AI順位は生スコア(較正前)で決める。較正後スコアは階段状で同値に潰れるため。
        # 生スコアが同値の馬は同順位とする(競馬の同着に相当。機械的なタイブレークはしない)。
        ai_rank_map = _rank_entries(rank_score_map, reverse=True)
        value_odds_map = {
            e.id: e.pre_race_odds if e.pre_race_odds is not None else e.odds
            for e in race.entries
        }
        odds_rank_map = _rank_entries(
            {
                e.id: value
                for e in race.entries
                if (value := value_odds_map[e.id]) is not None and value > 0
            }
        )
        entry_count = len(race.entries)
        pair_count = entry_count * (entry_count - 1) // 2
        odds_rows_by_type: dict[str, set[str]] = {}
        for row in race.odds:
            odds_rows_by_type.setdefault(row.bet_type, set()).add(row.combination)
        odds_status = [
            {
                "bet_type": "単勝",
                "available": sum(
                    1
                    for e in race.entries
                    if (e.pre_race_odds is not None and e.pre_race_odds > 0)
                    or (e.odds is not None and e.odds > 0)
                ),
                "total": entry_count,
            },
            {
                "bet_type": "複勝",
                "available": len(odds_rows_by_type.get("複勝", set())),
                "total": entry_count,
            },
            {
                "bet_type": "馬連",
                "available": len(odds_rows_by_type.get("馬連", set())),
                "total": pair_count,
            },
            {
                "bet_type": "ワイド",
                "available": len(odds_rows_by_type.get("ワイド", set())),
                "total": pair_count,
            },
        ]

        # 表示順も生スコアで決め、ai_rank と一致させる(同値は同順位=同着)
        ranked_entries = sorted(
            [e for e in race.entries if e.id in score_map],
            key=lambda e: rank_score_map[e.id],
            reverse=True,
        )
        score_gap = None
        race_shape = None
        if len(ranked_entries) >= 2:
            score_gap = score_map[ranked_entries[0].id] - score_map[ranked_entries[1].id]
            if score_gap >= 0.05:
                race_shape = "本命寄り"
            elif score_gap <= 0.02:
                race_shape = "混戦"
            else:
                race_shape = "やや混戦"

        top_ai = [
            {
                "entry_id": e.id,
                "horse_number": e.horse_number,
                "horse_id": e.horse_id,
                "horse_name": e.horse_name,
                "score": score_map[e.id],
                "ai_rank": ai_rank_map.get(e.id),
                "odds": value_odds_map[e.id],
                "odds_rank": odds_rank_map.get(e.id),
                "expected_value": (
                    score_map[e.id] * value_odds_map[e.id]
                    if value_odds_map[e.id] is not None and value_odds_map[e.id] > 0
                    else None
                ),
            }
            for e in ranked_entries[:3]
        ]
        candidate_entries = {e.id: e for e in race.entries}
        bet_candidates = [
            {
                "bet_type": c.bet_type,
                "entry_id": c.entry_id,
                "horse_number": candidate_entries[c.entry_id].horse_number
                if c.entry_id in candidate_entries
                else None,
                "horse_name": candidate_entries[c.entry_id].horse_name
                if c.entry_id in candidate_entries
                else None,
                "combination": c.combination,
                "probability": c.probability,
                "odds": c.odds,
                "expected_value": c.expected_value,
            }
            for c in betting.build_bet_candidates(
                race,
                [p for p in race.predictions if p.model_version == model_version],
                betting_config,
            )
        ]

        entries = [
            {
                "id": e.id,
                "horse_number": e.horse_number,
                "horse_id": e.horse_id,
                "horse_name": e.horse_name,
                "sex": e.sex,
                "age": e.age,
                "jockey": e.jockey,
                "jockey_id": e.jockey_id,
                "trainer": e.trainer,
                "trainer_id": e.trainer_id,
                "weight": e.weight,
                "horse_weight": e.horse_weight,
                "horse_weight_diff": e.horse_weight_diff,
                "odds": e.odds,
                "pre_race_odds": e.pre_race_odds,
                "final_odds": e.final_odds,
                "popularity": e.popularity if e.popularity is not None else odds_rank_map.get(e.id),
                "finish_position": e.finish_position,
                "score": score_map.get(e.id),
                "ai_rank": ai_rank_map.get(e.id),
                "odds_rank": odds_rank_map.get(e.id),
                "expected_value": (
                    score_map[e.id] * value_odds_map[e.id]
                    if e.id in score_map
                    and value_odds_map[e.id] is not None
                    and value_odds_map[e.id] > 0
                    else None
                ),
                "value_label": (
                    "妙味あり"
                    if e.id in score_map
                    and value_odds_map[e.id] is not None
                    and value_odds_map[e.id] > 0
                    and score_map[e.id] * value_odds_map[e.id] >= betting_config.min_expected_value
                    else "見送り"
                    if e.id in score_map
                    and value_odds_map[e.id] is not None
                    and value_odds_map[e.id] > 0
                    else None
                ),
                "ai_vs_odds": (
                    "AI評価高め"
                    if e.id in ai_rank_map
                    and e.id in odds_rank_map
                    and ai_rank_map[e.id] < odds_rank_map[e.id]
                    else None
                ),
                "has_bet": e.id in bet_entry_ids,
            }
            for e in sorted(race.entries, key=lambda e: e.horse_number)
        ]
        bets = [
            {
                "id": b.id,
                "mode": b.mode,
                "status": b.status,
                "bet_type": b.bet_type,
                "horse_number": b.entry.horse_number if b.entry else None,
                "combination": b.combination,
                "amount": b.amount,
                "odds_at_bet": b.odds_at_bet,
                "model_version": b.model_version,
                "payout": b.payout,
                "is_settled": b.is_settled,
                "placed_at": _iso(b.placed_at),
            }
            for b in visible_bets
        ]
        return {
            "id": race.id,
            "race_key": race.race_key,
            "race_date": _iso(race.race_date),
            "venue": race.venue,
            "race_number": race.race_number,
            "race_name": race.race_name,
            "start_time": _iso(race.start_time),
            "distance": race.distance,
            "track_type": race.track_type,
            "direction": race.direction,
            "going": race.going,
            "weather": race.weather,
            "race_class": race.race_class,
            "model_version": model_version,
            "analysis": {
                "top_ai": top_ai,
                "score_gap": score_gap,
                "race_shape": race_shape,
                "odds_status": odds_status,
            },
            "entries": entries,
            "bet_candidates": bet_candidates,
            "bets": bets,
            "collection_status": collection_status,
        }
    finally:
        session.close()
