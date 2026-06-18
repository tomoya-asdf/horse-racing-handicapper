import logging
from datetime import date, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler

from src.collector import scraper
from src.common import jobs
from src.common.config import settings
from src.common.db import get_session, init_db
from src.common.dynamic_config import load_scheduled_job_config
from src.common.models import (
    Entry,
    Horse,
    HorsePedigree,
    HorseResult,
    KaisaiDate,
    Race,
    RaceCollectionStatus,
)
from src.common.timeutils import now_jst

# races 起点の成績収集の種別(RaceCollectionStatus.kind と一致)
KIND_HORSE = "horse_results"

logging.basicConfig(level=logging.INFO)
# 5秒間隔のジョブポーリングがINFOログを埋め尽くすため、APSchedulerのログは抑制する
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# 結果の取得を試みる期間。これより古いレースは(開催中止等で結果が
# 取得できないままでも)対象から外し、再スクレイピングを打ち切る
RESULT_FETCH_DAYS = 7


def _upsert_races(races: list[dict]) -> None:
    session = get_session()
    try:
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
    finally:
        session.close()


def _update_finished_results() -> int:
    """確定したレースの着順をDBへ反映し、反映できたレース数を返す。"""
    updated = 0
    session = get_session()
    day_cache: dict = {}
    try:
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
            final_odds_by_number = {
                item["horse_number"]: item.get("odds")
                for item in latest["entries"]
                if item.get("odds") is not None
            } if latest is not None else {}
            for entry in race.entries:
                if entry.horse_number in positions:
                    entry.finish_position = positions[entry.horse_number]
                if entry.horse_number in final_odds_by_number:
                    entry.final_odds = final_odds_by_number[entry.horse_number]
                    entry.odds = final_odds_by_number[entry.horse_number]
            updated += 1

        session.commit()
    finally:
        session.close()
    return updated


# ---- races 起点の収集ドライバ共通部 ----

def _races_needing_collection(session, kind: str, limit: int):
    """まだ ``kind`` の収集が済んでいないレースを新しい順に最大 ``limit`` 件返す。"""
    done = session.query(RaceCollectionStatus.race_id).filter(RaceCollectionStatus.kind == kind)
    return (
        session.query(Race)
        .filter(Race.id.notin_(done))
        .order_by(Race.race_date.desc(), Race.id.desc())
        .limit(limit)
        .all()
    )


def _mark_race_collected(session, race_id: int, kind: str) -> None:
    exists = (
        session.query(RaceCollectionStatus.id)
        .filter_by(race_id=race_id, kind=kind)
        .first()
    )
    if exists is None:
        session.add(RaceCollectionStatus(race_id=race_id, kind=kind))


# ---- 馬の過去成績・血統 ----

def _upsert_horse_results(
    session, horse_id: str, name: str | None, results: list[dict], sire: dict | None = None
) -> None:
    """1頭分の過去成績を horse_results へ**追記のみ**で反映し、馬マスタを更新する。

    既存行は消さず、未保存の race_key だけを追加する(継続収集で履歴を積み増す)。
    ``sire`` を渡すと父(sire_id/sire_name)も同じ horse 行に保存する。
    """
    existing = {
        key
        for (key,) in session.query(HorseResult.race_key)
        .filter(HorseResult.horse_id == horse_id, HorseResult.race_key.isnot(None))
        .all()
    }
    had_any = bool(existing)
    seen: set[str] = set()
    for row in results:
        key = row.get("race_key")
        if key is not None:
            if key in existing or key in seen:
                continue
            seen.add(key)
        elif had_any:
            # race_key 無し行は再取得時に重複しやすいので、既存がある馬では追加しない
            continue
        session.add(HorseResult(horse_id=horse_id, **row))

    horse = session.get(Horse, horse_id)
    if horse is None:
        horse = Horse(horse_id=horse_id)
        session.add(horse)
    if name:
        horse.name = name
    if sire and sire.get("sire_id"):
        horse.sire_id = sire["sire_id"]
        horse.sire_name = sire["sire_name"]
    horse.results_fetched_at = now_jst()


def _upsert_horse_pedigree(session, horse_id: str, ancestors: list[dict]) -> None:
    """5代血統表の先祖を horse_pedigree へ**追記のみ**で保存する((generation, position) 一意)。"""
    existing = {
        (gen, pos)
        for gen, pos in session.query(HorsePedigree.generation, HorsePedigree.position)
        .filter(HorsePedigree.horse_id == horse_id)
        .all()
    }
    for anc in ancestors:
        slot = (anc["generation"], anc["position"])
        if slot in existing:
            continue
        existing.add(slot)
        session.add(
            HorsePedigree(
                horse_id=horse_id,
                generation=anc["generation"],
                position=anc["position"],
                ancestor_horse_id=anc.get("horse_id"),
                ancestor_name=anc.get("name"),
            )
        )


def _horses_with_pedigree(session, horse_ids: list[str]) -> set[str]:
    """``horse_ids`` のうち血統(horse_pedigree)を取得済みの馬IDを返す。"""
    if not horse_ids:
        return set()
    return {
        row[0]
        for row in session.query(HorsePedigree.horse_id)
        .filter(HorsePedigree.horse_id.in_(horse_ids))
        .distinct()
        .all()
    }


def _fresh_horses(session, horse_ids: list[str], refresh_days: int) -> set[str]:
    """``horse_ids`` のうち、過去成績を ``refresh_days`` 日以内に取得済みの馬IDを返す。"""
    if not horse_ids:
        return set()
    cutoff = now_jst() - timedelta(days=refresh_days)
    return {
        row[0]
        for row in session.query(Horse.horse_id)
        .filter(
            Horse.horse_id.in_(horse_ids),
            Horse.results_fetched_at.isnot(None),
            Horse.results_fetched_at >= cutoff,
        )
        .all()
    }


def _fetch_and_store_one_horse(horse_id: str, need_pedigree: bool) -> bool:
    """1頭の過去成績(と未取得なら5代血統)を取得・保存する。成功で True。"""
    try:
        data = scraper.fetch_horse_results(horse_id)
    except Exception as exc:
        logger.warning("failed to fetch horse results horse_id=%s: %s", horse_id, exc)
        return False

    ped = None
    if need_pedigree:
        try:
            ped = scraper.fetch_horse_pedigree_full(horse_id, settings.HORSE_PEDIGREE_GENERATIONS)
        except Exception as exc:
            logger.warning("failed to fetch pedigree horse_id=%s: %s", horse_id, exc)

    session = get_session()
    try:
        _upsert_horse_results(session, horse_id, data["name"], data["results"], ped)
        if ped and ped.get("ancestors"):
            _upsert_horse_pedigree(session, horse_id, ped["ancestors"])
        session.commit()
        return True
    except Exception:
        session.rollback()
        logger.exception("failed to upsert horse results horse_id=%s", horse_id)
        return False
    finally:
        session.close()


def _update_horse_results(max_races: int) -> int:
    """races 起点で、未収集レースの出走馬の過去成績・血統を集める。処理レース数を返す。"""
    if max_races <= 0:
        return 0
    session = get_session()
    try:
        races = _races_needing_collection(session, KIND_HORSE, max_races)
        race_infos = [
            (race.id, [e.horse_id for e in race.entries if e.horse_id]) for race in races
        ]
    finally:
        session.close()

    fetched_horses: set[str] = set()
    processed = 0
    for race_id, horse_ids in race_infos:
        unique_ids = list(dict.fromkeys(horse_ids))
        session = get_session()
        try:
            ped_known = _horses_with_pedigree(session, unique_ids)
            fresh = _fresh_horses(session, unique_ids, settings.HORSE_RESULTS_REFRESH_DAYS)
        finally:
            session.close()
        for horse_id in unique_ids:
            if horse_id in fetched_horses:
                continue
            fetched_horses.add(horse_id)
            # 過去成績が鮮度内かつ血統も取得済みなら、ネットワークアクセスせずスキップ
            if horse_id in fresh and horse_id in ped_known:
                continue
            _fetch_and_store_one_horse(horse_id, horse_id not in ped_known)
        session = get_session()
        try:
            _mark_race_collected(session, race_id, KIND_HORSE)
            session.commit()
        finally:
            session.close()
        processed += 1
    return processed


def _store_kaisai_dates(dates: set) -> None:
    """netkeiba開催カレンダーの開催日を kaisai_dates テーブルへ反映する(冪等)。"""
    if not dates:
        return
    session = get_session()
    try:
        existing = {
            row[0]
            for row in session.query(KaisaiDate.kaisai_date)
            .filter(KaisaiDate.kaisai_date.in_(dates))
            .all()
        }
        now = now_jst()
        for d in dates:
            if d in existing:
                continue
            session.add(KaisaiDate(kaisai_date=d, fetched_at=now))
        session.commit()
    finally:
        session.close()


def collect_kaisai_dates(start, end) -> frozenset:
    """``start``〜``end`` の各年月の開催カレンダーを取得してDBへ保存し、範囲内の開催日集合を返す。

    カレンダーは月単位で取得し、取得できた月は**その月全体**の開催日を保存する
    (カレンダー上は開催日だが未収集、の判別に使うため)。月の取得に失敗した場合は
    安全側に倒し、その月の全日付を「開催日候補」として返す(収集の取りこぼし防止)。
    """
    if start > end:
        return frozenset()
    in_range: set = set()
    to_store: set = set()
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        month_start = date(year, month, 1)
        next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
        month_end = date(next_year, next_month, 1) - timedelta(days=1)
        lo, hi = max(start, month_start), min(end, month_end)
        kaisai = scraper.fetch_kaisai_dates(year, month)
        if not kaisai:
            in_range.update(lo + timedelta(days=i) for i in range((hi - lo).days + 1))
        else:
            to_store.update(kaisai)
            in_range.update(d for d in kaisai if lo <= d <= hi)
        year, month = next_year, next_month
    _store_kaisai_dates(to_store)
    return frozenset(in_range)


def _run_collect(params: dict) -> str:
    # JRAは主に土日開催のため、当日だけでなく数日先まで収集する。開催カレンダーを
    # 先に取得・保存し、開催日にだけ出馬表を取りに行くことで、開催の無い日への
    # リクエストを省く(netkeiba負荷軽減)。
    today = now_jst().date()
    end = today + timedelta(days=settings.COLLECT_DAYS_AHEAD)
    kaisai = collect_kaisai_dates(today, end)
    total = 0
    for offset in range(settings.COLLECT_DAYS_AHEAD + 1):
        target = today + timedelta(days=offset)
        if target not in kaisai:
            continue
        races = scraper.fetch_upcoming_races(target)
        _upsert_races(races)
        total += len(races)
    updated = _update_finished_results()
    return (
        f"取得レース={total}件({today}〜{end}, 開催{len(kaisai)}日), "
        f"結果反映={updated}件"
    )


def _collect_races_limit(params: dict) -> int:
    """1回の収集で処理する未収集レース数の上限(params.limit 優先、既定は設定値)。"""
    default_limit = settings.RESULTS_RACES_PER_RUN
    try:
        return int(params.get("limit", default_limit)) if params else default_limit
    except (TypeError, ValueError):
        return default_limit


def _run_collect_horses(params: dict) -> str:
    """未収集レースの出走馬について、過去成績と5代血統をまとめて収集する手動ジョブ。"""
    limit = _collect_races_limit(params)
    processed = _update_horse_results(limit)
    return f"馬の過去成績・血統を{processed}レース分収集しました(上限{limit}レース)"


def _run_backfill(params: dict) -> str:
    """WebUIからの過去データ一括取得。paramsの日付範囲はAPI側で検証済み。"""
    from datetime import date

    from src.collector import backfill  # 循環import回避のため遅延import

    start = date.fromisoformat(params["start_date"])
    end = date.fromisoformat(params["end_date"])
    return backfill.backfill(start, end)


def _scheduled_collect() -> None:
    config = load_scheduled_job_config(jobs.COLLECT)
    if config is None or not config.enabled:
        return
    if not jobs.scheduled_run_due(
        jobs.COLLECT,
        config.interval_minutes,
        weekdays=config.weekdays,
        exact_time=config.exact_time,
    ):
        return
    jobs.run_scheduled(jobs.COLLECT, _run_collect)


def _scheduled_collect_horses() -> None:
    config = load_scheduled_job_config(jobs.COLLECT_HORSES)
    if config is None or not config.enabled:
        return
    if not jobs.scheduled_run_due(
        jobs.COLLECT_HORSES,
        config.interval_minutes,
        weekdays=config.weekdays,
        exact_time=config.exact_time,
    ):
        return
    jobs.run_scheduled(jobs.COLLECT_HORSES, _run_collect_horses)


def _poll_queued_jobs() -> None:
    handlers = {
        jobs.COLLECT: _run_collect,
        jobs.BACKFILL: _run_backfill,
        jobs.COLLECT_HORSES: _run_collect_horses,
    }
    jobs.enqueue_due_reservations(list(handlers))
    jobs.process_queued(handlers)


def main() -> None:
    init_db()
    jobs.recover_stale(
        [
            jobs.COLLECT,
            jobs.BACKFILL,
            jobs.COLLECT_HORSES,
        ]
    )
    scheduler = BlockingScheduler(timezone="Asia/Tokyo")
    scheduler.add_job(_scheduled_collect, "interval", minutes=1)
    scheduler.add_job(_scheduled_collect_horses, "interval", minutes=1)
    scheduler.add_job(
        _poll_queued_jobs, "interval", seconds=jobs.POLL_INTERVAL_SECONDS
    )
    logger.info("collector started: interval=%s min", settings.COLLECT_INTERVAL_MINUTES)
    _scheduled_collect()
    _scheduled_collect_horses()
    scheduler.start()


if __name__ == "__main__":
    main()
