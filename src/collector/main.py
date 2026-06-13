import logging
from datetime import date, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler

from src.collector import scraper
from src.common import jobs
from src.common.config import settings
from src.common.db import get_session, init_db
from src.common.dynamic_config import load_scheduled_job_config
from src.common.models import Entry, Horse, HorseResult, Race
from src.common.timeutils import now_jst

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
                    entry.odds = entry_data["odds"]
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
            for entry in race.entries:
                if entry.horse_number in positions:
                    entry.finish_position = positions[entry.horse_number]
            updated += 1

        session.commit()
    finally:
        session.close()
    return updated


def _upsert_horse_results(
    session, horse_id: str, name: str | None, results: list[dict], sire: dict | None = None
) -> None:
    """1頭分の過去成績をhorse_resultsへ反映し、馬マスタ(名前・父・取得時刻)を更新する。

    後から走が増えるため、再取得時は既存行を全件入れ替えて重複なく保つ。
    取得を試みた事実をhorsesに記録し、新馬(0件)を毎回取りに行かないようにする。
    ``sire`` を渡すと父(sire_id/sire_name)も同じhorse行に保存する。
    """
    session.query(HorseResult).filter(HorseResult.horse_id == horse_id).delete()
    # (horse_id, race_key) は一意。同一fetch内で同じrace_keyが重複した場合に備えて
    # 非nullのrace_keyの重複を除く(race_key=Noneの行は複数あってよい)
    seen_keys: set[str] = set()
    for row in results:
        key = row.get("race_key")
        if key is not None:
            if key in seen_keys:
                continue
            seen_keys.add(key)
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


def _horse_ids_to_fetch(session, limit: int) -> list[str]:
    """過去成績が未取得、または取得が古い馬のIDを差分的に返す。"""
    stale_before = now_jst() - timedelta(days=settings.HORSE_RESULTS_REFRESH_DAYS)
    rows = (
        session.query(Entry.horse_id)
        .outerjoin(Horse, Horse.horse_id == Entry.horse_id)
        .filter(Entry.horse_id.isnot(None), Entry.horse_id != "")
        .filter((Horse.horse_id.is_(None)) | (Horse.results_fetched_at < stale_before))
        .distinct()
        .limit(limit)
        .all()
    )
    return [row[0] for row in rows]


def _update_horse_results(limit: int) -> int:
    """出走馬のうち過去成績が未取得・古い馬を最大limit頭まで収集する。

    netkeibaへの負荷を抑えるため頭数を制限し、馬ごとに短いトランザクションで
    コミットする(途中失敗で前の馬の成果を失わないため)。
    """
    if limit <= 0:
        return 0
    session = get_session()
    try:
        horse_ids = _horse_ids_to_fetch(session, limit)
        # 父(sire)は1頭1回取れば十分なので、既に取得済みの馬はスキップする
        sire_known = (
            {
                row[0]
                for row in session.query(Horse.horse_id)
                .filter(Horse.horse_id.in_(horse_ids), Horse.sire_id.isnot(None))
                .all()
            }
            if horse_ids
            else set()
        )
    finally:
        session.close()

    fetched = 0
    for horse_id in horse_ids:
        try:
            data = scraper.fetch_horse_results(horse_id)
        except Exception as exc:
            logger.warning("failed to fetch horse results horse_id=%s: %s", horse_id, exc)
            continue

        ped = None
        if horse_id not in sire_known:
            try:
                ped = scraper.fetch_horse_pedigree(horse_id)
            except Exception as exc:
                logger.warning("failed to fetch pedigree horse_id=%s: %s", horse_id, exc)

        session = get_session()
        try:
            _upsert_horse_results(session, horse_id, data["name"], data["results"], ped)
            session.commit()
            fetched += 1
        except Exception:
            session.rollback()
            logger.exception("failed to upsert horse results horse_id=%s", horse_id)
        finally:
            session.close()
    return fetched


def update_horse_results_for_race_dates(start: date, end: date) -> int:
    """Backfill後のモデル学習用に、期間内に出走した馬の過去戦績と血統を補完する。"""
    stale_before = now_jst() - timedelta(days=settings.HORSE_RESULTS_REFRESH_DAYS)
    session = get_session()
    try:
        rows = (
            session.query(Entry.horse_id)
            .join(Race, Race.id == Entry.race_id)
            .outerjoin(Horse, Horse.horse_id == Entry.horse_id)
            .filter(Race.race_date >= start, Race.race_date <= end)
            .filter(Entry.horse_id.isnot(None), Entry.horse_id != "")
            .filter(
                (Horse.horse_id.is_(None))
                | (Horse.results_fetched_at.is_(None))
                | (Horse.results_fetched_at < stale_before)
            )
            .distinct()
            .all()
        )
        horse_ids = [row[0] for row in rows]
        sire_known = (
            {
                row[0]
                for row in session.query(Horse.horse_id)
                .filter(Horse.horse_id.in_(horse_ids), Horse.sire_id.isnot(None))
                .all()
            }
            if horse_ids
            else set()
        )
    finally:
        session.close()

    logger.info("backfill horse result targets: %d horses", len(horse_ids))
    fetched = 0
    for horse_id in horse_ids:
        try:
            data = scraper.fetch_horse_results(horse_id)
        except Exception as exc:
            logger.warning("failed to fetch horse results horse_id=%s: %s", horse_id, exc)
            continue

        ped = None
        if horse_id not in sire_known:
            try:
                ped = scraper.fetch_horse_pedigree(horse_id)
            except Exception as exc:
                logger.warning("failed to fetch pedigree horse_id=%s: %s", horse_id, exc)

        session = get_session()
        try:
            _upsert_horse_results(session, horse_id, data["name"], data["results"], ped)
            session.commit()
            fetched += 1
        except Exception:
            session.rollback()
            logger.exception("failed to upsert horse results horse_id=%s", horse_id)
        finally:
            session.close()
    return fetched


def _run_collect(params: dict) -> str:
    # JRAは主に土日開催のため、当日だけでなく数日先まで収集する
    # (開催の無い日はレース一覧が空で返るだけなので、リクエスト数は1日1回分増えるのみ)
    today = now_jst().date()
    total = 0
    for offset in range(settings.COLLECT_DAYS_AHEAD + 1):
        races = scraper.fetch_upcoming_races(today + timedelta(days=offset))
        _upsert_races(races)
        total += len(races)
    updated = _update_finished_results()
    horses = _update_horse_results(settings.HORSE_RESULTS_PER_RUN)
    return (
        f"取得レース={total}件"
        f"({today}〜{today + timedelta(days=settings.COLLECT_DAYS_AHEAD)}), "
        f"結果反映={updated}件, 馬成績収集={horses}頭"
    )


def _run_collect_horses(params: dict) -> str:
    """馬の過去成績だけをまとめて収集する手動ジョブ。

    定期収集(collect)でも少しずつ収集するが、初回の埋め込みを早めたいときに
    手動で繰り返し実行する用。1回の上限はparamsのlimit、無ければ既定の5倍。
    """
    default_limit = settings.HORSE_RESULTS_PER_RUN * 5
    try:
        limit = int(params.get("limit", default_limit)) if params else default_limit
    except (TypeError, ValueError):
        limit = default_limit
    fetched = _update_horse_results(limit)
    return f"馬の過去成績を{fetched}頭分収集しました(上限{limit}頭)"


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
        jobs.COLLECT, config.interval_minutes, weekdays=config.weekdays
    ):
        return
    jobs.run_scheduled(jobs.COLLECT, _run_collect)


def _scheduled_collect_horses() -> None:
    config = load_scheduled_job_config(jobs.COLLECT_HORSES)
    if config is None or not config.enabled:
        return
    if not jobs.scheduled_run_due(
        jobs.COLLECT_HORSES, config.interval_minutes, weekdays=config.weekdays
    ):
        return
    jobs.run_scheduled(jobs.COLLECT_HORSES, _run_collect_horses)


def _poll_queued_jobs() -> None:
    jobs.process_queued(
        {
            jobs.COLLECT: _run_collect,
            jobs.BACKFILL: _run_backfill,
            jobs.COLLECT_HORSES: _run_collect_horses,
        }
    )


def main() -> None:
    init_db()
    jobs.recover_stale([jobs.COLLECT, jobs.BACKFILL, jobs.COLLECT_HORSES])
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
