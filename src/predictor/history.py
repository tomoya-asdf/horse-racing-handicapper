"""馬の過去成績(horse_results)から、リークの無い履歴特徴量を作成する。

最重要な前提: あるレース(開催日 D)の特徴量には、**D より前**に行われた
過去成績だけを使う。これを守らないと「未来の結果」を学習に混ぜてしまい、
バックテストの回収率が過大評価される。

方針はオッズ不使用(コミット4617a41)を踏襲し、ここで作る特徴量も着順・タイム・
距離・馬場種別・出走間隔といった成績ベースのものに限定する(市場人気・オッズは使わない)。
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

from src.common.feature_catalog import (  # noqa: F401  再エクスポート(features.py 等が参照)
    HISTORY_FEATURES,
    JOCKEY_HISTORY_FEATURES,
    TRAINER_HISTORY_FEATURES,
)
from src.common.models import Entry, Horse, HorseResult, Race

# 同距離とみなす許容差(m)。1600m戦なら1400〜1800mの実績を「同距離帯」として集計する
SAME_DISTANCE_TOLERANCE = 200


def season_features(race_date: date | None) -> tuple[float, float]:
    """開催日を年内の周期(1年=2π)に写像した sin / cos を返す。

    距離・馬場と違い「季節」は連続かつ周期的(12月と1月が近い)なので、月番号を
    そのまま数値にすると年末年始で不連続になる。sin/cos の2成分にすることで
    周期性を保ったままモデルへ季節変動を渡せる。取得不能時は (NaN, NaN)。
    """
    if race_date is None:
        return float("nan"), float("nan")
    day_of_year = race_date.timetuple().tm_yday
    angle = 2 * math.pi * day_of_year / 365.25
    return math.sin(angle), math.cos(angle)


def load_horse_history(
    session, horse_ids: "list[str] | set[str] | None" = None
) -> dict[str, pd.DataFrame]:
    """馬の過去成績を horse_id ごとの DataFrame にまとめて返す。

    レースごとにDBへ問い合わせると遅いため、学習/予測の開始時に一括ロードして
    メモリ上で日付フィルタする。``horse_ids`` を渡すとその馬だけに絞ってロードする
    (予測は対象レースの出走馬のみで足り、全件スキャンを避けられる)。None なら全件。
    """
    if horse_ids is not None and not horse_ids:
        return {}
    query = session.query(
        HorseResult.horse_id,
        HorseResult.race_date,
        HorseResult.finish_position,
        HorseResult.distance,
        HorseResult.track_type,
        HorseResult.going,
        HorseResult.last_3f,
    ).filter(HorseResult.horse_id.isnot(None))
    if horse_ids is not None:
        query = query.filter(HorseResult.horse_id.in_(list(horse_ids)))
    rows = query.all()
    if not rows:
        return {}

    df = pd.DataFrame(
        rows,
        columns=[
            "horse_id",
            "race_date",
            "finish_position",
            "distance",
            "track_type",
            "going",
            "last_3f",
        ],
    )
    df["race_date"] = pd.to_datetime(df["race_date"], errors="coerce")

    history: dict[str, pd.DataFrame] = {}
    for horse_id, group in df.groupby("horse_id"):
        history[str(horse_id)] = group.reset_index(drop=True)
    return history


def load_sire_map(
    session, horse_ids: "list[str] | set[str] | None" = None
) -> dict[str, str]:
    """horse_id -> sire_id(父の馬ID)のマップを返す。血統(父)特徴量に使う。

    ``horse_ids`` を渡すとその馬だけに絞る(予測の出走馬のみ)。None なら全件。
    """
    if horse_ids is not None and not horse_ids:
        return {}
    query = session.query(Horse.horse_id, Horse.sire_id).filter(Horse.sire_id.isnot(None))
    if horse_ids is not None:
        query = query.filter(Horse.horse_id.in_(list(horse_ids)))
    return {str(horse_id): str(sire_id) for horse_id, sire_id in query.all()}


def _load_person_history_from_entries(
    session, id_column: str, person_ids: "list[str] | set[str] | None" = None
) -> dict[str, pd.DataFrame]:
    """騎手/調教師の履歴を、収集済みの出走表(entries × races)から直接組み立てる。

    騎手・調教師は多くのレースに騎乗/出走するため、自前に蓄積した確定レース
    (finish_position あり)だけで recent10 等の近走特徴量を十分カバーできる
    (個別ページのスクレイピングは不要)。距離・馬場はレース側(``races``)から取る。

    ``person_ids`` を渡すとその騎手/調教師だけに絞る(予測の出走馬の関係者のみ)。
    """
    if person_ids is not None and not person_ids:
        return {}
    person_id = getattr(Entry, id_column)
    query = (
        session.query(
            person_id,
            Race.race_date,
            Entry.finish_position,
            Race.distance,
            Race.track_type,
            Race.going,
        )
        .join(Race, Race.id == Entry.race_id)
        .filter(person_id.isnot(None), Entry.finish_position.isnot(None))
    )
    if person_ids is not None:
        query = query.filter(person_id.in_(list(person_ids)))
    rows = query.all()
    if not rows:
        return {}

    df = pd.DataFrame(
        rows,
        columns=["person_id", "race_date", "finish_position", "distance", "track_type", "going"],
    )
    df["race_date"] = pd.to_datetime(df["race_date"], errors="coerce")
    history: dict[str, pd.DataFrame] = {}
    for person_id_value, group in df.groupby("person_id"):
        history[str(person_id_value)] = group.reset_index(drop=True)
    return history


def load_jockey_history(
    session, jockey_ids: "list[str] | set[str] | None" = None
) -> dict[str, pd.DataFrame]:
    return _load_person_history_from_entries(session, "jockey_id", jockey_ids)


def load_trainer_history(
    session, trainer_ids: "list[str] | set[str] | None" = None
) -> dict[str, pd.DataFrame]:
    return _load_person_history_from_entries(session, "trainer_id", trainer_ids)


def _empty_features() -> dict[str, float]:
    feats: dict[str, float] = {name: np.nan for name in HISTORY_FEATURES}
    feats["career_starts"] = 0
    feats["same_dist_starts"] = 0
    feats["same_surface_starts"] = 0
    feats["same_going_starts"] = 0
    return feats


def _empty_person_features(prefix: str) -> dict[str, float]:
    feats = {
        f"{prefix}_starts": 0,
        f"{prefix}_win_rate": np.nan,
        f"{prefix}_place_rate": np.nan,
        f"{prefix}_avg_finish_recent10": np.nan,
        f"{prefix}_same_dist_starts": 0,
        f"{prefix}_same_dist_win_rate": np.nan,
        f"{prefix}_same_surface_starts": 0,
        f"{prefix}_same_surface_win_rate": np.nan,
        f"{prefix}_same_going_starts": 0,
        f"{prefix}_same_going_win_rate": np.nan,
    }
    return feats


def compute_person_history_features(
    past: pd.DataFrame | None,
    prefix: str,
    race_date: date | None,
    distance: int | None,
    track_type: str | None,
    going: str | None = None,
) -> dict[str, float]:
    feats = _empty_person_features(prefix)
    if past is None or race_date is None:
        return feats

    cutoff = pd.Timestamp(race_date)
    past = past[past["race_date"].notna() & (past["race_date"] < cutoff)]
    if past.empty:
        return feats
    past = past.sort_values("race_date", ascending=False)

    finished = past[past["finish_position"].notna()]
    starts = int(len(finished))
    feats[f"{prefix}_starts"] = starts
    if starts <= 0:
        return feats

    finish = finished["finish_position"].astype(float)
    feats[f"{prefix}_win_rate"] = float((finish == 1).mean())
    feats[f"{prefix}_place_rate"] = float((finish <= 3).mean())
    feats[f"{prefix}_avg_finish_recent10"] = float(finish.head(10).mean())

    if distance is not None:
        dist = finished["distance"].astype(float)
        same_dist = finished[dist.notna() & ((dist - distance).abs() <= SAME_DISTANCE_TOLERANCE)]
        feats[f"{prefix}_same_dist_starts"] = int(len(same_dist))
        if len(same_dist) > 0:
            feats[f"{prefix}_same_dist_win_rate"] = float(
                (same_dist["finish_position"].astype(float) == 1).mean()
            )

    if track_type is not None:
        same_surface = finished[finished["track_type"] == track_type]
        feats[f"{prefix}_same_surface_starts"] = int(len(same_surface))
        if len(same_surface) > 0:
            feats[f"{prefix}_same_surface_win_rate"] = float(
                (same_surface["finish_position"].astype(float) == 1).mean()
            )

    if going is not None and "going" in finished.columns:
        same_going = finished[finished["going"] == going]
        feats[f"{prefix}_same_going_starts"] = int(len(same_going))
        if len(same_going) > 0:
            feats[f"{prefix}_same_going_win_rate"] = float(
                (same_going["finish_position"].astype(float) == 1).mean()
            )

    return feats


def compute_history_features(
    past: pd.DataFrame | None,
    race_date: date | None,
    distance: int | None,
    track_type: str | None,
    going: str | None = None,
) -> dict[str, float]:
    """1頭分の履歴特徴量を返す。``past`` はその馬の全過去成績(日付フィルタ前)。"""
    feats = _empty_features()
    if past is None or race_date is None:
        return feats

    cutoff = pd.Timestamp(race_date)
    past = past[past["race_date"].notna() & (past["race_date"] < cutoff)]
    if past.empty:
        return feats
    past = past.sort_values("race_date", ascending=False)

    finished = past[past["finish_position"].notna()]
    starts = int(len(finished))
    feats["career_starts"] = starts
    if starts > 0:
        finish = finished["finish_position"].astype(float)
        feats["win_rate"] = float((finish == 1).mean())
        feats["place_rate"] = float((finish <= 3).mean())
        feats["avg_finish_recent3"] = float(finish.head(3).mean())
        feats["avg_finish_recent5"] = float(finish.head(5).mean())
        # 直近10走(成績ベース。古い実績の影響を抑えた近走の調子)
        recent10 = finish.head(10)
        feats["win_rate_recent10"] = float((recent10 == 1).mean())
        feats["place_rate_recent10"] = float((recent10 <= 3).mean())

    last3f = past.head(5)["last_3f"].dropna().astype(float)
    if not last3f.empty:
        feats["best_last3f_recent5"] = float(last3f.min())
        feats["avg_last3f_recent5"] = float(last3f.mean())

    last_date = past["race_date"].iloc[0]
    feats["days_since_last"] = float((cutoff - last_date).days)

    last_distance = past["distance"].iloc[0]
    if distance is not None and not pd.isna(last_distance):
        feats["distance_change"] = float(distance) - float(last_distance)

    if distance is not None and starts > 0:
        dist = finished["distance"].astype(float)
        same_dist = finished[dist.notna() & ((dist - distance).abs() <= SAME_DISTANCE_TOLERANCE)]
        feats["same_dist_starts"] = int(len(same_dist))
        if len(same_dist) > 0:
            feats["same_dist_avg_finish"] = float(same_dist["finish_position"].astype(float).mean())

    if track_type is not None and starts > 0:
        same_surface = finished[finished["track_type"] == track_type]
        feats["same_surface_starts"] = int(len(same_surface))
        if len(same_surface) > 0:
            feats["same_surface_avg_finish"] = float(
                same_surface["finish_position"].astype(float).mean()
            )

    if going is not None and starts > 0 and "going" in finished.columns:
        same_going = finished[finished["going"] == going]
        feats["same_going_starts"] = int(len(same_going))
        if len(same_going) > 0:
            feats["same_going_place_rate"] = float(
                (same_going["finish_position"].astype(float) <= 3).mean()
            )

    return feats


def build_entries_frame(
    entries,
    race,
    history: dict[str, pd.DataFrame],
    sire_map: dict[str, str] | None = None,
    jockey_history: dict[str, pd.DataFrame] | None = None,
    trainer_history: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """1レースの出走馬から、build_features に渡す入力DataFrameを組み立てる。

    ``entries`` は Entry ORM のリスト、``race`` は Race ORM。戻り値は entry.id を
    インデックスとし、基礎列(馬番・斤量・騎手ID・距離・父ID)＋履歴特徴量を持つ。
    学習・予測の双方がこの関数を通すことで、同一の特徴量定義を保証する。
    """
    sire_map = sire_map or {}
    jockey_history = jockey_history or {}
    trainer_history = trainer_history or {}
    # 季節(sin/cos)はレース単位で同じ値。出走馬ごとに再計算せず一度だけ求める
    season_sin, season_cos = season_features(race.race_date)
    field_size = len(entries)
    # 相対特徴量のためレース内平均を一度だけ求める(欠損は除いて平均、全欠損なら NaN)
    weight_mean = _mean_or_nan([e.weight for e in entries])
    horse_weight_mean = _mean_or_nan([e.horse_weight for e in entries])
    rows = []
    index = []
    for entry in entries:
        feats = compute_history_features(
            history.get(entry.horse_id) if entry.horse_id else None,
            race.race_date,
            race.distance,
            race.track_type,
            race.going,
        )
        jockey_feats = compute_person_history_features(
            jockey_history.get(entry.jockey_id) if entry.jockey_id else None,
            "jockey",
            race.race_date,
            race.distance,
            race.track_type,
            race.going,
        )
        trainer_feats = compute_person_history_features(
            trainer_history.get(entry.trainer_id) if entry.trainer_id else None,
            "trainer",
            race.race_date,
            race.distance,
            race.track_type,
            race.going,
        )
        rows.append(
            {
                "horse_number": entry.horse_number,
                "sex": entry.sex,
                "age": entry.age,
                "weight": entry.weight,
                "horse_weight": entry.horse_weight,
                "horse_weight_diff": entry.horse_weight_diff,
                "jockey_id": entry.jockey_id,
                "trainer_id": entry.trainer_id,
                "sire_id": sire_map.get(entry.horse_id) if entry.horse_id else None,
                "distance": race.distance,
                "season_sin": season_sin,
                "season_cos": season_cos,
                # 枠順・相対値(レース内での相対位置)
                "draw_ratio": (
                    float(entry.horse_number) / field_size
                    if entry.horse_number is not None and field_size > 0
                    else np.nan
                ),
                "weight_rel": _rel(entry.weight, weight_mean),
                "horse_weight_rel": _rel(entry.horse_weight, horse_weight_mean),
                # レース条件(レース単位で同じ値)
                "race_number": race.race_number,
                "track_type": race.track_type,
                "going": race.going,
                "weather": race.weather,
                "direction": race.direction,
                "race_class": race.race_class,
                "venue": race.venue,
                **feats,
                **jockey_feats,
                **trainer_feats,
            }
        )
        index.append(entry.id)
    return pd.DataFrame(rows, index=index)


def _mean_or_nan(values: list) -> float:
    nums = [float(v) for v in values if v is not None and not pd.isna(v)]
    return float(sum(nums) / len(nums)) if nums else float("nan")


def _rel(value, mean: float) -> float:
    """value − レース平均。value 欠損または平均が NaN のときは NaN。"""
    if value is None or pd.isna(value) or pd.isna(mean):
        return float("nan")
    return float(value) - float(mean)
