"""races 起点の馬の過去成績・5代血統の収集。

各レースの出走馬について db.netkeiba.com の馬ページから過去成績(と未取得なら
血統)を集め、``race_collection_status`` に収集済みを記録する。
"""

import logging
from datetime import timedelta

from src.collector import scraper
from src.common.config import settings
from src.common.db import get_session
from src.common.models import (
    Horse,
    HorsePedigree,
    HorseResult,
    Race,
    RaceCollectionStatus,
)
from src.common.timeutils import now_jst

logger = logging.getLogger(__name__)

# races 起点の成績収集の種別(RaceCollectionStatus.kind と一致)
KIND_HORSE = "horse_results"


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
