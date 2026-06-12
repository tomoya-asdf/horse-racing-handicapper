import pandas as pd

FEATURE_COLUMNS = ["horse_number", "weight", "field_size", "jockey"]
CATEGORICAL_FEATURES = ["jockey"]

DEFAULT_WEIGHT = 55.0
DEFAULT_JOCKEY = "unknown"


def build_features(entries: pd.DataFrame) -> pd.DataFrame:
    """Build odds-independent model features for one race.

    Expected columns are horse_number, weight, and jockey. The returned frame keeps
    the same index as the input so scores can be mapped back to entry IDs.
    """
    weight = entries["weight"].astype(float)
    mean_weight = weight.mean()
    if pd.isna(mean_weight):
        mean_weight = DEFAULT_WEIGHT
    weight_filled = weight.fillna(mean_weight)

    jockey = entries.get("jockey", pd.Series(DEFAULT_JOCKEY, index=entries.index))
    jockey_filled = jockey.fillna(DEFAULT_JOCKEY).replace("", DEFAULT_JOCKEY).astype(str)

    df = pd.DataFrame(index=entries.index)
    df["horse_number"] = entries["horse_number"].astype(float)
    df["weight"] = weight_filled
    df["field_size"] = float(len(entries))
    df["jockey"] = jockey_filled.astype("category")

    return df[FEATURE_COLUMNS]
