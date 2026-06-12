"""予測モデルの学習スクリプト。

DBに蓄積された確定済みレース(entries.finish_position が設定済み)から
特徴量とラベル(1着=1, それ以外=0)を作成し、LightGBMの二値分類モデルを学習する。

実行方法:
    docker compose run --rm predictor python -m src.predictor.train
"""

import logging
from datetime import date, datetime

import joblib
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split

from src.common.db import get_session, init_db
from src.common.models import Entry, Race
from src.predictor.features import CATEGORICAL_FEATURES, FEATURE_COLUMNS, build_features
from src.predictor.model import MODEL_PATH

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MIN_RACES = 20


def _prepare_training_data(frames: list[pd.DataFrame]) -> pd.DataFrame:
    data = pd.concat(frames)
    for column in CATEGORICAL_FEATURES:
        data[column] = data[column].astype("category")
    return data


def _load_training_frames(before: date | None = None) -> tuple[list[pd.DataFrame], int]:
    """確定済みレースの特徴量+ラベルをレース単位のDataFrameリストで返す。

    ``before`` を指定すると、その日付より前のレースに限定する
    (バックテスト時に検証期間のデータを学習から除外するため)。
    """
    session = get_session()
    try:
        query = (
            session.query(Race)
            .join(Entry)
            .filter(Entry.finish_position.isnot(None))
        )
        if before is not None:
            query = query.filter(Race.race_date < before)
        races = query.distinct().all()

        frames: list[pd.DataFrame] = []
        for race in races:
            entries = [e for e in race.entries if e.finish_position is not None]
            if len(entries) < 2:
                continue

            entries_df = pd.DataFrame(
                {
                    "horse_number": [e.horse_number for e in entries],
                    "weight": [e.weight for e in entries],
                    "jockey": [e.jockey for e in entries],
                },
                index=[e.id for e in entries],
            )
            features = build_features(entries_df)
            features["label"] = [int(e.finish_position == 1) for e in entries]
            frames.append(features)

        return frames, len(frames)
    finally:
        session.close()


def train_model() -> str:
    """モデルを学習・保存し、結果の要約を返す(WebUIのジョブ実行からも呼ばれる)。"""
    frames, race_count = _load_training_frames()

    if race_count < MIN_RACES:
        return f"学習データ不足(レース数={race_count}, 必要数={MIN_RACES})のためスキップしました"

    data = _prepare_training_data(frames)

    if data["label"].nunique() < 2:
        return "学習データに1着の記録が無いためスキップしました"

    # 検証はレース単位で分割する。同一レースの行を学習と検証の両方に入れると、
    # レース内の相対特徴(odds_rank等)を通じて検証AUCが楽観的になるため
    train_frames, valid_frames = train_test_split(frames, test_size=0.2, random_state=42)
    train_data = _prepare_training_data(train_frames)
    valid_data = _prepare_training_data(valid_frames)

    auc = None
    if train_data["label"].nunique() > 1 and valid_data["label"].nunique() > 1:
        eval_model = LGBMClassifier(objective="binary", random_state=42)
        eval_model.fit(
            train_data[FEATURE_COLUMNS],
            train_data["label"],
            categorical_feature=CATEGORICAL_FEATURES,
        )
        auc = roc_auc_score(
            valid_data["label"], eval_model.predict_proba(valid_data[FEATURE_COLUMNS])[:, 1]
        )
        logger.info("validation AUC: %.4f (valid races=%d)", auc, len(valid_frames))

    # 保存するモデルは全データで学習する(分割は検証AUCの算出のためだけに使う)
    model = LGBMClassifier(objective="binary", random_state=42)
    model.fit(data[FEATURE_COLUMNS], data["label"], categorical_feature=CATEGORICAL_FEATURES)

    version = datetime.now().strftime("%Y%m%d-%H%M%S")
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": model,
            "feature_columns": FEATURE_COLUMNS,
            "categorical_features": CATEGORICAL_FEATURES,
            "version": version,
        },
        MODEL_PATH,
    )
    summary = f"モデルを保存しました(version={version}, レース={race_count}件, 行数={len(data)}"
    if auc is not None:
        summary += f", 検証AUC={auc:.4f}"
    return summary + ")"


def main() -> None:
    init_db()
    logger.info(train_model())


if __name__ == "__main__":
    main()
