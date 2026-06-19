"""騎手・調教師の戦績 API(収集済みの出走表から構成)。"""

from fastapi import APIRouter, HTTPException
from sqlalchemy import extract
from sqlalchemy.orm import selectinload

from src.common.db import session_scope
from src.common.models import Entry, Race

router = APIRouter()


def _person_detail(
    id_attr: str, name_attr: str, partner_attr: str, person_id: str, year: int | None = None
) -> dict:
    """騎手/調教師の戦績を、収集済みの出走表(entries × races)から構成して返す。

    騎手/調教師の過去成績は個別ページをスクレイプせず、自前に蓄積した出走データから
    そのまま組み立てる(特徴量も同じ entries から作る)。

    戦績は年度(``year``)単位で返す。指定が無い/未収集の年度なら最新年度を使い、
    収集済みの全年度を ``years`` として返してUI側で切り替えられるようにする。
    """
    with session_scope() as session:
        person_col = getattr(Entry, id_attr)
        year_col = extract("year", Race.race_date)
        year_rows = (
            session.query(year_col)
            .select_from(Entry)
            .join(Race, Race.id == Entry.race_id)
            .filter(person_col == person_id, Race.race_date.isnot(None))
            .distinct()
            .all()
        )
        years = sorted({int(row[0]) for row in year_rows if row[0] is not None}, reverse=True)
        if not years:
            raise HTTPException(status_code=404, detail=f"{name_attr} not found")
        selected_year = year if year in years else years[0]

        entries = (
            session.query(Entry)
            .join(Race, Race.id == Entry.race_id)
            .options(selectinload(Entry.race).selectinload(Race.entries))
            .filter(person_col == person_id, year_col == selected_year)
            .order_by(Race.race_date.desc().nullslast(), Entry.id.desc())
            .all()
        )
        name = next((getattr(e, name_attr) for e in entries if getattr(e, name_attr)), None)

        results = []
        for e in entries:
            r = e.race
            results.append(
                {
                    "race_key": r.race_key,
                    "race_date": r.race_date.isoformat() if r.race_date else None,
                    "venue": r.venue,
                    "race_name": r.race_name,
                    "field_size": len(r.entries) if r.entries else None,
                    "horse_id": e.horse_id,
                    "horse_name": e.horse_name,
                    "horse_number": e.horse_number,
                    partner_attr: getattr(e, partner_attr),
                    f"{partner_attr}_id": getattr(e, f"{partner_attr}_id"),
                    "weight": e.weight,
                    "odds": e.odds,
                    "popularity": e.popularity,
                    "finish_position": e.finish_position,
                    "distance": r.distance,
                    "track_type": r.track_type,
                    "going": r.going,
                }
            )
        return {
            f"{name_attr}_id": person_id,
            "name": name,
            "results_fetched_at": None,
            "years": years,
            "selected_year": selected_year,
            "results": results,
        }


@router.get("/api/jockeys/{jockey_id}")
def jockey_detail(jockey_id: str, year: int | None = None) -> dict:
    return _person_detail("jockey_id", "jockey", "trainer", jockey_id, year)


@router.get("/api/trainers/{trainer_id}")
def trainer_detail(trainer_id: str, year: int | None = None) -> dict:
    return _person_detail("trainer_id", "trainer", "jockey", trainer_id, year)
