import numpy as np
import pandas as pd

from src.predictor.history import HISTORY_FEATURES, JOCKEY_HISTORY_FEATURES, TRAINER_HISTORY_FEATURES

# 基礎特徴量(レース当日の出馬表から分かるもの。オッズ・人気は使わない)
# age=馬齢, horse_weight=馬体重, horse_weight_diff=前走比増減, season_sin/cos=季節(周期)
BASE_NUMERIC_FEATURES = [
    "horse_number",
    "age",
    "weight",
    "horse_weight",
    "horse_weight_diff",
    "field_size",
    "distance",
    "season_sin",
    "season_cos",
]
# カテゴリ特徴量: 性別・騎手ID・調教師ID(いずれも同姓同名対策にID)・父ID(血統=距離/芝ダ適性の遺伝)
CATEGORICAL_FEATURES = ["sex", "jockey_id", "trainer_id", "sire_id"]
# モデルに渡す全特徴量(基礎 + カテゴリ + 馬の過去成績ベースの履歴特徴量)
FEATURE_COLUMNS = (
    BASE_NUMERIC_FEATURES
    + CATEGORICAL_FEATURES
    + HISTORY_FEATURES
    + JOCKEY_HISTORY_FEATURES
    + TRAINER_HISTORY_FEATURES
)

DEFAULT_WEIGHT = 55.0
DEFAULT_SEX = "unknown"
DEFAULT_JOCKEY_ID = "unknown"
DEFAULT_TRAINER_ID = "unknown"
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

    def _categorical(column: str, default: str) -> pd.Series:
        series = entries.get(column, pd.Series(default, index=entries.index))
        return series.fillna(default).replace("", default).astype(str).astype("category")

    def _numeric(column: str) -> pd.Series:
        return entries.get(column, pd.Series(np.nan, index=entries.index)).astype(float)

    df = pd.DataFrame(index=entries.index)
    df["horse_number"] = entries["horse_number"].astype(float)
    # 馬齢・馬体重・増減・季節(sin/cos)は欠損(NaN)のままLightGBMに渡す
    df["age"] = _numeric("age")
    df["weight"] = weight_filled
    df["horse_weight"] = _numeric("horse_weight")
    df["horse_weight_diff"] = _numeric("horse_weight_diff")
    df["field_size"] = float(len(entries))
    df["distance"] = _numeric("distance")
    df["season_sin"] = _numeric("season_sin")
    df["season_cos"] = _numeric("season_cos")
    df["sex"] = _categorical("sex", DEFAULT_SEX)
    df["jockey_id"] = _categorical("jockey_id", DEFAULT_JOCKEY_ID)
    df["trainer_id"] = _categorical("trainer_id", DEFAULT_TRAINER_ID)
    df["sire_id"] = _categorical("sire_id", DEFAULT_SIRE_ID)
    for column in HISTORY_FEATURES:
        df[column] = _numeric(column)
    for column in JOCKEY_HISTORY_FEATURES + TRAINER_HISTORY_FEATURES:
        df[column] = _numeric(column)

    return df[FEATURE_COLUMNS]
