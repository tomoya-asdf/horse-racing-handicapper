import numpy as np
import pandas as pd

from src.predictor.history import HISTORY_FEATURES

# 基礎特徴量(レース当日の出馬表から分かるもの。オッズ・人気は使わない)
BASE_NUMERIC_FEATURES = ["horse_number", "weight", "field_size", "distance"]
# カテゴリ特徴量: 騎手ID(同姓同名対策)と父ID(血統=距離・芝ダ適性の遺伝)
CATEGORICAL_FEATURES = ["jockey_id", "sire_id"]
# モデルに渡す全特徴量(基礎 + カテゴリ + 馬の過去成績ベースの履歴特徴量)
FEATURE_COLUMNS = BASE_NUMERIC_FEATURES + CATEGORICAL_FEATURES + HISTORY_FEATURES

DEFAULT_WEIGHT = 55.0
DEFAULT_JOCKEY_ID = "unknown"
DEFAULT_SIRE_ID = "unknown"


def build_features(entries: pd.DataFrame) -> pd.DataFrame:
    """1レース分のモデル特徴量を作る(オッズ不使用)。

    入力 ``entries`` は ``history.build_entries_frame`` が組み立てた DataFrame を想定し、
    馬番・斤量・騎手ID・距離・各履歴特徴量(HISTORY_FEATURES)を列に持つ。戻り値は
    入力と同じインデックス(entry.id)を保ち、スコアを出走馬へ対応付けられるようにする。

    騎手は同姓同名がありうるため、騎手名ではなく一意な netkeiba 騎手ID(jockey_id)を
    カテゴリ特徴量として使う。履歴特徴量は欠損(NaN)のまま渡し、LightGBMの欠損処理に任せる。
    """
    weight = entries["weight"].astype(float)
    mean_weight = weight.mean()
    if pd.isna(mean_weight):
        mean_weight = DEFAULT_WEIGHT
    weight_filled = weight.fillna(mean_weight)

    jockey_id = entries.get("jockey_id", pd.Series(DEFAULT_JOCKEY_ID, index=entries.index))
    jockey_id_filled = jockey_id.fillna(DEFAULT_JOCKEY_ID).replace("", DEFAULT_JOCKEY_ID).astype(str)

    sire_id = entries.get("sire_id", pd.Series(DEFAULT_SIRE_ID, index=entries.index))
    sire_id_filled = sire_id.fillna(DEFAULT_SIRE_ID).replace("", DEFAULT_SIRE_ID).astype(str)

    df = pd.DataFrame(index=entries.index)
    df["horse_number"] = entries["horse_number"].astype(float)
    df["weight"] = weight_filled
    df["field_size"] = float(len(entries))
    df["distance"] = entries.get("distance", pd.Series(np.nan, index=entries.index)).astype(float)
    df["jockey_id"] = jockey_id_filled.astype("category")
    df["sire_id"] = sire_id_filled.astype("category")
    for column in HISTORY_FEATURES:
        df[column] = entries.get(column, pd.Series(np.nan, index=entries.index)).astype(float)

    return df[FEATURE_COLUMNS]
