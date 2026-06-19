"""レース・出走表のDB反映と、確定レースの着順取り込み。"""

import logging
from datetime import timedelta

from src.collector import scraper
from src.common.db import session_scope
from src.common.models import Entry, Race
from src.common.timeutils import now_jst

logger = logging.getLogger(__name__)

# 結果の取得を試みる期間。これより古いレースは(開催中止等で結果が
# 取得できないままでも)対象から外し、再スクレイピングを打ち切る
RESULT_FETCH_DAYS = 7


def upsert_races(races: list[dict]) -> None:
    with session_scope() as session:
        for race_data in races:
            race = session.query(Race).filter_by(race_key=race_data["race_key"]).one_or_none()
            if race is None:
                race = Race(race_key=race_data["race_key"])
                session.add(race)

            race.race_date = race_data["race_date"]
            race.venue = race_data["venue"]
            race.race_number = race_data["race_number"]
            race.race_name = race_data["race_name"]
            race.start_time = race_data["start_time"]
            # レース条件は取得できた項目だけ更新する(馬場・天候は当日に判明し、
            # 数日先の収集ではNoneのため、既存値を消さない)
            for field in ("distance", "track_type", "direction", "going", "weather", "race_class"):
                value = race_data.get(field)
                if value is not None:
                    setattr(race, field, value)
            session.flush()  # 新規レースのIDを確定させる

            for entry_data in race_data["entries"]:
                entry = (
                    session.query(Entry)
                    .filter_by(race_id=race.id, horse_number=entry_data["horse_number"])
                    .one_or_none()
                )
                if entry is None:
                    entry = Entry(race_id=race.id, horse_number=entry_data["horse_number"])
                    session.add(entry)

                entry.horse_name = entry_data["horse_name"]
                entry.horse_id = entry_data.get("horse_id")
                entry.sex = entry_data.get("sex")
                entry.age = entry_data.get("age")
                entry.jockey = entry_data["jockey"]
                entry.jockey_id = entry_data.get("jockey_id")
                entry.trainer = entry_data.get("trainer")
                entry.trainer_id = entry_data.get("trainer_id")
                entry.weight = entry_data["weight"]
                # オッズ・人気・馬体重は収集の度に更新するが、取得できなかった(None)場合に
                # 既存の値を消さないよう、値があるときだけ上書きする
                # (馬体重は当日計量のため、数日先の収集ではNoneのことが多い)
                if entry_data.get("odds") is not None:
                    odds = entry_data["odds"]
                    entry.odds = odds
                    if race.start_time is not None and race.start_time <= now_jst():
                        entry.final_odds = odds
                    else:
                        entry.pre_race_odds = odds
                if entry_data.get("popularity") is not None:
                    entry.popularity = entry_data["popularity"]
                if entry_data.get("horse_weight") is not None:
                    entry.horse_weight = entry_data["horse_weight"]
                if entry_data.get("horse_weight_diff") is not None:
                    entry.horse_weight_diff = entry_data["horse_weight_diff"]

        session.commit()


def update_finished_results() -> int:
    """確定したレースの着順をDBへ反映し、反映できたレース数を返す。"""
    updated = 0
    day_cache: dict = {}
    with session_scope() as session:
        now = now_jst()
        races = (
            session.query(Race)
            .filter(
                Race.start_time.isnot(None),
                Race.start_time < now,
                Race.race_date >= (now - timedelta(days=RESULT_FETCH_DAYS)).date(),
            )
            .all()
        )
        for race in races:
            if not race.entries:
                continue
            # 一度でも結果を反映済みのレースはスキップする。出走取消・除外馬の
            # finish_position は確定後もNoneのままなので、all()での判定は不可
            if any(entry.finish_position is not None for entry in race.entries):
                continue

            try:
                result = scraper.fetch_race_results(race.race_key)
            except Exception as exc:
                logger.warning("failed to fetch results for race_key=%s: %s", race.race_key, exc)
                continue

            positions = {e["horse_number"]: e["finish_position"] for e in result["entries"]}
            if not positions:
                continue
            if race.race_date not in day_cache:
                day_cache[race.race_date] = scraper.fetch_upcoming_races(
                    race.race_date,
                    include_started=True,
                )
            latest = next(
                (item for item in day_cache[race.race_date] if item["race_key"] == race.race_key),
                None,
            )
            latest_by_number = (
                {item["horse_number"]: item for item in latest["entries"]}
                if latest is not None
                else {}
            )
            for entry in race.entries:
                if entry.horse_number in positions:
                    entry.finish_position = positions[entry.horse_number]
                latest_entry = latest_by_number.get(entry.horse_number)
                if latest_entry is None:
                    continue
                # 確定オッズ。発走後の収集で取得した最終オッズで上書きする
                if latest_entry.get("odds") is not None:
                    entry.final_odds = latest_entry["odds"]
                    entry.odds = latest_entry["odds"]
                # 馬体重は当日計量のため発走直前まで取得できないことが多い。
                # 発走後の収集で初めて取れることがあるため、ここでも更新する
                if latest_entry.get("horse_weight") is not None:
                    entry.horse_weight = latest_entry["horse_weight"]
                if latest_entry.get("horse_weight_diff") is not None:
                    entry.horse_weight_diff = latest_entry["horse_weight_diff"]
            updated += 1

        session.commit()
    return updated
