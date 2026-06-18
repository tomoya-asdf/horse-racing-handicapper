import joblib
import pandas as pd

from src.common.paths import MODEL_PATH
from src.predictor.features import FEATURE_COLUMNS


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


def predict_scores(model_bundle: dict, features: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """(生スコア, 較正後スコア) を返す。

    - 生スコア: LightGBM の predict_proba(1着確率)。ほぼ全馬で異なるため、レース内の
      **順位付け**はこちらを使うと同順位(タイ)が生じない。
    - 較正後スコア: 等張回帰の較正器を通した確率。**期待値計算(score×オッズ)や確率表示**
      に使う。等張回帰は階段状で同値に潰れるため順位付けには不向き。

    較正器が無ければ両者は同じ値になる。
    """
    feature_columns = model_bundle.get("feature_columns", FEATURE_COLUMNS)
    x = features[feature_columns]
    raw = model_bundle["model"].predict_proba(x)[:, 1]

    calibrator = model_bundle.get("calibrator")
    calibrated = calibrator.predict(raw) if calibrator is not None else raw

    return (
        pd.Series(raw, index=features.index, name="raw_score"),
        pd.Series(calibrated, index=features.index, name="score"),
    )


def predict(model_bundle: dict, features: pd.DataFrame) -> pd.Series:
    """特徴量から予測スコア(較正後の1着確率)を算出する。"""
    _, calibrated = predict_scores(model_bundle, features)
    return calibrated
