"""馬詳細(基本情報・血統・過去戦績)の API。"""

from fastapi import APIRouter, HTTPException

from src.api.serializers import _iso
from src.common.db import get_session
from src.common.models import Entry, Horse, HorsePedigree, HorseResult

router = APIRouter()


@router.get("/api/horses/{horse_id}")
def horse_detail(horse_id: str) -> dict:
    session = get_session()
    try:
        horse = session.get(Horse, horse_id)
        results = (
            session.query(HorseResult)
            .filter(HorseResult.horse_id == horse_id)
            .order_by(HorseResult.race_date.desc().nullslast(), HorseResult.id.desc())
            .limit(30)
            .all()
        )
        if horse is None and not results:
            entry = session.query(Entry).filter(Entry.horse_id == horse_id).first()
            if entry is None:
                raise HTTPException(status_code=404, detail="horse not found")
            name = entry.horse_name
            sire_id = None
            sire_name = None
            results_fetched_at = None
        else:
            name = horse.name if horse else None
            sire_id = horse.sire_id if horse else None
            sire_name = horse.sire_name if horse else None
            results_fetched_at = _iso(horse.results_fetched_at) if horse else None

        pedigree = (
            session.query(HorsePedigree)
            .filter(HorsePedigree.horse_id == horse_id)
            .order_by(HorsePedigree.generation.asc(), HorsePedigree.position.asc())
            .all()
        )

        return {
            "horse_id": horse_id,
            "name": name,
            "sire_id": sire_id,
            "sire_name": sire_name,
            "results_fetched_at": results_fetched_at,
            "pedigree": [
                {
                    "generation": p.generation,
                    "position": p.position,
                    "ancestor_horse_id": p.ancestor_horse_id,
                    "ancestor_name": p.ancestor_name,
                }
                for p in pedigree
            ],
            "results": [
                {
                    "race_key": r.race_key,
                    "race_date": r.race_date.isoformat() if r.race_date else None,
                    "venue": r.venue,
                    "race_name": r.race_name,
                    "field_size": r.field_size,
                    "horse_number": r.horse_number,
                    "odds": r.odds,
                    "popularity": r.popularity,
                    "finish_position": r.finish_position,
                    "jockey": r.jockey,
                    "jockey_id": r.jockey_id,
                    "weight": r.weight,
                    "distance": r.distance,
                    "track_type": r.track_type,
                    "going": r.going,
                    "time_seconds": r.time_seconds,
                    "last_3f": r.last_3f,
                    "horse_weight": r.horse_weight,
                }
                for r in results
            ],
        }
    finally:
        session.close()
