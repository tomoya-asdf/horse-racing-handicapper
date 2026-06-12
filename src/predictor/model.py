from pathlib import Path

import joblib
import pandas as pd

from src.predictor.features import FEATURE_COLUMNS

MODEL_PATH = Path("/app/data/model.pkl")


def load_model() -> dict:
    """学習済みモデルを読み込む。

    戻り値は ``{"model": ..., "feature_columns": [...], "version": str}`` の辞書。
    `src/predictor/train.py` で学習・保存したファイルを読み込む想定。
    """
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"モデルファイルが見つかりません: {MODEL_PATH}。"
            " `python -m src.predictor.train` を実行して学習してください。"
        )
    return joblib.load(MODEL_PATH)


def predict(model_bundle: dict, features: pd.DataFrame) -> pd.Series:
    """特徴量から予測スコア(1着になる確率)を算出する。"""
    feature_columns = model_bundle.get("feature_columns", FEATURE_COLUMNS)
    x = features[feature_columns]
    scores = model_bundle["model"].predict_proba(x)[:, 1]
    return pd.Series(scores, index=features.index, name="score")
