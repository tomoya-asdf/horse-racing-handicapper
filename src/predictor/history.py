"""馬の過去成績(horse_results)から、リークの無い履歴特徴量を作成する。

最重要な前提: あるレース(開催日 D)の特徴量には、**D より前**に行われた
過去成績だけを使う。これを守らないと「未来の結果」を学習に混ぜてしまい、
バックテストの回収率が過大評価される。

方針はオッズ不使用(コミット4617a41)を踏襲し、ここで作る特徴量も着順・タイム・
距離・馬場種別・出走間隔といった成績ベースのものに限定する(市場人気・オッズは使わない)。
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from src.common.models import Horse, HorseResult

# 同距離とみなす許容差(m)。1600m戦なら1400〜1800mの実績を「同距離帯」として集計する
SAME_DISTANCE_TOLERANCE = 200

# build_features が受け取る履歴特徴量の列名(全て数値、未該当はNaN=欠損としてLightGBMに渡す)
HISTORY_FEATURES = [
    "career_starts",  # 出走回数(完走数)
    "win_rate",  # 勝率
    "place_rate",  # 複勝率(3着内率)
    "avg_finish_recent3",  # 直近3走の平均着順
    "avg_finish_recent5",  # 直近5走の平均着順
    "best_last3f_recent5",  # 直近5走の上がり3F最速
    "avg_last3f_recent5",  # 直近5走の上がり3F平均
    "days_since_last",  # 前走からの間隔(日)
    "distance_change",  # 今回距離 - 前走距離
    "same_dist_starts",  # 同距離帯の出走数
    "same_dist_avg_finish",  # 同距離帯の平均着順
    "same_surface_starts",  # 同馬場種別(芝/ダ)の出走数
    "same_surface_avg_finish",  # 同馬場種別の平均着順
]


def load_horse_history(session) -> dict[str, pd.DataFrame]:
    """全馬の過去成績を horse_id ごとの DataFrame にまとめて返す。

    レースごとにDBへ問い合わせると遅いため、学習/予測の開始時に一括ロードして
    メモリ上で日付フィルタする。
    """
    rows = (
        session.query(
            HorseResult.horse_id,
            HorseResult.race_date,
            HorseResult.finish_position,
            HorseResult.distance,
            HorseResult.track_type,
            HorseResult.last_3f,
        )
        .filter(HorseResult.horse_id.isnot(None))
        .all()
    )
    if not rows:
        return {}

    df = pd.DataFrame(
        rows,
        columns=["horse_id", "race_date", "finish_position", "distance", "track_type", "last_3f"],
    )
    df["race_date"] = pd.to_datetime(df["race_date"], errors="coerce")

    history: dict[str, pd.DataFrame] = {}
    for horse_id, group in df.groupby("horse_id"):
        history[str(horse_id)] = group.reset_index(drop=True)
    return history


def load_sire_map(session) -> dict[str, str]:
    """horse_id -> sire_id(父の馬ID)のマップを返す。血統(父)特徴量に使う。"""
    rows = session.query(Horse.horse_id, Horse.sire_id).filter(Horse.sire_id.isnot(None)).all()
    return {str(horse_id): str(sire_id) for horse_id, sire_id in rows}


def _empty_features() -> dict[str, float]:
    feats: dict[str, float] = {name: np.nan for name in HISTORY_FEATURES}
    feats["career_starts"] = 0
    feats["same_dist_starts"] = 0
    feats["same_surface_starts"] = 0
    return feats


def compute_history_features(
    past: pd.DataFrame | None,
    race_date: date | None,
    distance: int | None,
    track_type: str | None,
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

    return feats


def build_entries_frame(
    entries, race, history: dict[str, pd.DataFrame], sire_map: dict[str, str] | None = None
) -> pd.DataFrame:
    """1レースの出走馬から、build_features に渡す入力DataFrameを組み立てる。

    ``entries`` は Entry ORM のリスト、``race`` は Race ORM。戻り値は entry.id を
    インデックスとし、基礎列(馬番・斤量・騎手ID・距離・父ID)＋履歴特徴量を持つ。
    学習・予測の双方がこの関数を通すことで、同一の特徴量定義を保証する。
    """
    sire_map = sire_map or {}
    rows = []
    index = []
    for entry in entries:
        feats = compute_history_features(
            history.get(entry.horse_id) if entry.horse_id else None,
            race.race_date,
            race.distance,
            race.track_type,
        )
        rows.append(
            {
                "horse_number": entry.horse_number,
                "weight": entry.weight,
                "jockey_id": entry.jockey_id,
                "sire_id": sire_map.get(entry.horse_id) if entry.horse_id else None,
                "distance": race.distance,
                **feats,
            }
        )
        index.append(entry.id)
    return pd.DataFrame(rows, index=index)
