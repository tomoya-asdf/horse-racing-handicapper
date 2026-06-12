import pandas as pd

FEATURE_COLUMNS = ["horse_number", "weight", "odds", "implied_prob", "odds_rank", "field_size"]

DEFAULT_WEIGHT = 55.0
DEFAULT_ODDS = 10.0


def build_features(entries: pd.DataFrame) -> pd.DataFrame:
    """出走馬データから予測モデル用の特徴量データフレームを作成する。

    ``entries`` は1レース分の出走馬データで、少なくとも
    ``horse_number`` / ``weight`` / ``odds`` 列を持つこと。
    戻り値は入力と同じindexを保持する(呼び出し側でentry_idと対応付けるため)。

    学習(train.py)と推論(predictor/main.py)の両方から、レース単位の
    DataFrameに対して呼び出されることを想定している。
    """
    weight = entries["weight"].astype(float)
    mean_weight = weight.mean()
    if pd.isna(mean_weight):
        mean_weight = DEFAULT_WEIGHT
    weight_filled = weight.fillna(mean_weight)

    odds = entries["odds"].astype(float)
    mean_odds = odds.mean()
    if pd.isna(mean_odds):
        mean_odds = DEFAULT_ODDS
    odds_filled = odds.fillna(mean_odds)
    safe_odds = odds_filled.where(odds_filled > 0, mean_odds)

    df = pd.DataFrame(index=entries.index)
    df["horse_number"] = entries["horse_number"].astype(float)
    df["weight"] = weight_filled
    df["odds"] = odds_filled
    df["implied_prob"] = 1.0 / safe_odds
    df["odds_rank"] = odds_filled.rank(method="min")
    df["field_size"] = float(len(entries))

    return df[FEATURE_COLUMNS]
