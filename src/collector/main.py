import logging
from datetime import timedelta

from apscheduler.schedulers.blocking import BlockingScheduler

from src.collector import scraper
from src.common import jobs
from src.common.config import settings
from src.common.db import get_session, init_db
from src.common.dynamic_config import load_scheduled_job_config
from src.common.models import (
    Entry,
    Horse,
    HorseResult,
    Jockey,
    JockeyResult,
    Race,
    Trainer,
    TrainerResult,
)
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


def _known_sire_ids(session, horse_ids: list[str]) -> set[str]:
    """``horse_ids`` のうち父(sire_id)が取得済みの馬IDを返す。

    父は1頭1回取れば十分なので、ここに含まれる馬は血統取得をスキップする。
    """
    if not horse_ids:
        return set()
    return {
        row[0]
        for row in session.query(Horse.horse_id)
        .filter(Horse.horse_id.in_(horse_ids), Horse.sire_id.isnot(None))
        .all()
    }


def _fetch_and_store_horse_results(horse_ids: list[str], sire_known: set[str]) -> int:
    """馬IDを順に取得し、過去成績(と未取得なら血統)を horse_results へ保存する。

    netkeibaへの負荷を抑えるため馬ごとに短いトランザクションでコミットし、途中失敗で
    前の馬の成果を失わないようにする。``sire_known`` の馬は父が取得済みのため血統取得を
    スキップする。保存できた頭数を返す。
    """
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
        sire_known = _known_sire_ids(session, horse_ids)
    finally:
        session.close()

    return _fetch_and_store_horse_results(horse_ids, sire_known)


def _upsert_jockey_results(session, jockey_id: str, name: str | None, results: list[dict]) -> None:
    session.query(JockeyResult).filter(JockeyResult.jockey_id == jockey_id).delete()
    seen_keys: set[tuple[str | None, str | None]] = set()
    for row in results:
        key = (row.get("race_key"), row.get("horse_id"))
        if key[0] is not None and key in seen_keys:
            continue
        seen_keys.add(key)
        session.add(JockeyResult(jockey_id=jockey_id, **row))

    jockey = session.get(Jockey, jockey_id)
    if jockey is None:
        jockey = Jockey(jockey_id=jockey_id)
        session.add(jockey)
    if name:
        jockey.name = name
    jockey.results_fetched_at = now_jst()


def _upsert_trainer_results(session, trainer_id: str, name: str | None, results: list[dict]) -> None:
    session.query(TrainerResult).filter(TrainerResult.trainer_id == trainer_id).delete()
    seen_keys: set[tuple[str | None, str | None]] = set()
    for row in results:
        key = (row.get("race_key"), row.get("horse_id"))
        if key[0] is not None and key in seen_keys:
            continue
        seen_keys.add(key)
        session.add(TrainerResult(trainer_id=trainer_id, **row))

    trainer = session.get(Trainer, trainer_id)
    if trainer is None:
        trainer = Trainer(trainer_id=trainer_id)
        session.add(trainer)
    if name:
        trainer.name = name
    trainer.results_fetched_at = now_jst()


def _jockey_ids_to_fetch(session, limit: int) -> list[str]:
    stale_before = now_jst() - timedelta(days=settings.JOCKEY_RESULTS_REFRESH_DAYS)
    rows = (
        session.query(Entry.jockey_id)
        .outerjoin(Jockey, Jockey.jockey_id == Entry.jockey_id)
        .filter(Entry.jockey_id.isnot(None), Entry.jockey_id != "")
        .filter((Jockey.jockey_id.is_(None)) | (Jockey.results_fetched_at < stale_before))
        .distinct()
        .limit(limit)
        .all()
    )
    return [row[0] for row in rows]


def _trainer_ids_to_fetch(session, limit: int) -> list[str]:
    stale_before = now_jst() - timedelta(days=settings.TRAINER_RESULTS_REFRESH_DAYS)
    rows = (
        session.query(Entry.trainer_id)
        .outerjoin(Trainer, Trainer.trainer_id == Entry.trainer_id)
        .filter(Entry.trainer_id.isnot(None), Entry.trainer_id != "")
        .filter((Trainer.trainer_id.is_(None)) | (Trainer.results_fetched_at < stale_before))
        .distinct()
        .limit(limit)
        .all()
    )
    return [row[0] for row in rows]


def _update_jockey_results(limit: int) -> int:
    if limit <= 0:
        return 0
    session = get_session()
    try:
        jockey_ids = _jockey_ids_to_fetch(session, limit)
    finally:
        session.close()

    fetched = 0
    for jockey_id in jockey_ids:
        try:
            data = scraper.fetch_jockey_results(jockey_id)
        except Exception as exc:
            logger.warning("failed to fetch jockey results jockey_id=%s: %s", jockey_id, exc)
            continue
        session = get_session()
        try:
            _upsert_jockey_results(session, jockey_id, data["name"], data["results"])
            session.commit()
            fetched += 1
        except Exception:
            session.rollback()
            logger.exception("failed to upsert jockey results jockey_id=%s", jockey_id)
        finally:
            session.close()
    return fetched


def _update_trainer_results(limit: int) -> int:
    if limit <= 0:
        return 0
    session = get_session()
    try:
        trainer_ids = _trainer_ids_to_fetch(session, limit)
    finally:
        session.close()

    fetched = 0
    for trainer_id in trainer_ids:
        try:
            data = scraper.fetch_trainer_results(trainer_id)
        except Exception as exc:
            logger.warning("failed to fetch trainer results trainer_id=%s: %s", trainer_id, exc)
            continue
        session = get_session()
        try:
            _upsert_trainer_results(session, trainer_id, data["name"], data["results"])
            session.commit()
            fetched += 1
        except Exception:
            session.rollback()
            logger.exception("failed to upsert trainer results trainer_id=%s", trainer_id)
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
    return (
        f"取得レース={total}件"
        f"({today}〜{today + timedelta(days=settings.COLLECT_DAYS_AHEAD)}), "
        f"結果反映={updated}件"
    )


def _run_collect_horses(params: dict) -> str:
    """馬の過去成績だけをまとめて収集する手動ジョブ。

    レース収集やバックフィルでは馬過去成績を取得しないため、このジョブでまとめて収集する。
    1回の上限はparamsのlimit、無ければ既定の5倍。
    """
    default_limit = settings.HORSE_RESULTS_PER_RUN * 5
    try:
        limit = int(params.get("limit", default_limit)) if params else default_limit
    except (TypeError, ValueError):
        limit = default_limit
    fetched = _update_horse_results(limit)
    return f"馬の過去成績を{fetched}頭分収集しました(上限{limit}頭)"


def _run_collect_jockeys(params: dict) -> str:
    default_limit = settings.JOCKEY_RESULTS_PER_RUN * 5
    try:
        limit = int(params.get("limit", default_limit)) if params else default_limit
    except (TypeError, ValueError):
        limit = default_limit
    fetched = _update_jockey_results(limit)
    return f"騎手の過去戦績を{fetched}人分収集しました(上限{limit}人)"


def _run_collect_trainers(params: dict) -> str:
    default_limit = settings.TRAINER_RESULTS_PER_RUN * 5
    try:
        limit = int(params.get("limit", default_limit)) if params else default_limit
    except (TypeError, ValueError):
        limit = default_limit
    fetched = _update_trainer_results(limit)
    return f"調教師の過去戦績を{fetched}人分収集しました(上限{limit}人)"


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


def _scheduled_collect_jockeys() -> None:
    config = load_scheduled_job_config(jobs.COLLECT_JOCKEYS)
    if config is None or not config.enabled:
        return
    if not jobs.scheduled_run_due(
        jobs.COLLECT_JOCKEYS, config.interval_minutes, weekdays=config.weekdays
    ):
        return
    jobs.run_scheduled(jobs.COLLECT_JOCKEYS, _run_collect_jockeys)


def _scheduled_collect_trainers() -> None:
    config = load_scheduled_job_config(jobs.COLLECT_TRAINERS)
    if config is None or not config.enabled:
        return
    if not jobs.scheduled_run_due(
        jobs.COLLECT_TRAINERS, config.interval_minutes, weekdays=config.weekdays
    ):
        return
    jobs.run_scheduled(jobs.COLLECT_TRAINERS, _run_collect_trainers)


def _poll_queued_jobs() -> None:
    jobs.process_queued(
        {
            jobs.COLLECT: _run_collect,
            jobs.BACKFILL: _run_backfill,
            jobs.COLLECT_HORSES: _run_collect_horses,
            jobs.COLLECT_JOCKEYS: _run_collect_jockeys,
            jobs.COLLECT_TRAINERS: _run_collect_trainers,
        }
    )


def main() -> None:
    init_db()
    jobs.recover_stale(
        [
            jobs.COLLECT,
            jobs.BACKFILL,
            jobs.COLLECT_HORSES,
            jobs.COLLECT_JOCKEYS,
            jobs.COLLECT_TRAINERS,
        ]
    )
    scheduler = BlockingScheduler(timezone="Asia/Tokyo")
    scheduler.add_job(_scheduled_collect, "interval", minutes=1)
    scheduler.add_job(_scheduled_collect_horses, "interval", minutes=1)
    scheduler.add_job(_scheduled_collect_jockeys, "interval", minutes=1)
    scheduler.add_job(_scheduled_collect_trainers, "interval", minutes=1)
    scheduler.add_job(
        _poll_queued_jobs, "interval", seconds=jobs.POLL_INTERVAL_SECONDS
    )
    logger.info("collector started: interval=%s min", settings.COLLECT_INTERVAL_MINUTES)
    _scheduled_collect()
    _scheduled_collect_horses()
    _scheduled_collect_jockeys()
    _scheduled_collect_trainers()
    scheduler.start()


if __name__ == "__main__":
    main()
