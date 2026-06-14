"""予測モデルの学習スクリプト。

DBに蓄積された確定済みレース(entries.finish_position が設定済み)から
特徴量とラベル(1着=1, それ以外=0)を作成し、LightGBMの二値分類モデルを学習する。

実行方法:
    docker compose run --rm predictor python -m src.predictor.train
"""

import logging
import json
from datetime import date, datetime

import joblib
import pandas as pd
from lightgbm import LGBMClassifier, early_stopping, log_evaluation
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import log_loss, roc_auc_score

from src.common.db import get_session, init_db
from src.common.models import Entry, ModelVersion, Race
from src.common.paths import MODEL_PATH
from src.predictor.features import CATEGORICAL_FEATURES, FEATURE_COLUMNS, build_features
from src.predictor.history import (
    build_entries_frame,
    load_horse_history,
    load_jockey_history,
    load_sire_map,
    load_trainer_history,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MIN_RACES = 20
# 時系列分割で検証に回す割合(新しい側) と early stopping の打ち切りラウンド数
VALID_FRACTION = 0.2
EARLY_STOPPING_ROUNDS = 50
MAX_BOOST_ROUNDS = 1000
DEFAULT_BOOST_ROUNDS = 100


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
        # 時系列分割のため古い順に並べる(frames の並び順がそのまま時系列になる)
        races = query.distinct().order_by(Race.race_date, Race.id).all()

        # 全馬の過去成績を一括ロードし、各レースの開催日より前の成績だけで
        # 履歴特徴量を作る(history側で日付フィルタしリークを防ぐ)
        history = load_horse_history(session)
        sire_map = load_sire_map(session)
        jockey_history = load_jockey_history(session)
        trainer_history = load_trainer_history(session)

        frames: list[pd.DataFrame] = []
        for race in races:
            entries = [e for e in race.entries if e.finish_position is not None]
            if len(entries) < 2:
                continue

            entries_df = build_entries_frame(
                entries, race, history, sire_map, jockey_history, trainer_history
            )
            features = build_features(entries_df)
            features["label"] = pd.Series(
                {e.id: int(e.finish_position == 1) for e in entries}
            )
            frames.append(features)

        return frames, len(frames)
    finally:
        session.close()


def _evaluate_with_time_split(
    frames: list[pd.DataFrame],
) -> tuple[float | None, float | None, int | None, IsotonicRegression | None, int]:
    """時系列分割(古い側で学習・新しい側で検証)で評価する。

    戻り値は (検証AUC, 検証logloss, 最適な木の本数, 確率較正器, 検証レース数)。
    検証に使えるデータが無い/片側クラスのみの場合は AUC等を None で返す。
    """
    split = int(len(frames) * (1 - VALID_FRACTION))
    train_frames = frames[:split]
    valid_frames = frames[split:]
    if not train_frames or not valid_frames:
        return None, None, None, None, 0

    train_data = _prepare_training_data(train_frames)
    valid_data = _prepare_training_data(valid_frames)
    if train_data["label"].nunique() < 2 or valid_data["label"].nunique() < 2:
        return None, None, None, None, len(valid_frames)

    eval_model = LGBMClassifier(
        objective="binary", n_estimators=MAX_BOOST_ROUNDS, random_state=42
    )
    eval_model.fit(
        train_data[FEATURE_COLUMNS],
        train_data["label"],
        categorical_feature=CATEGORICAL_FEATURES,
        eval_set=[(valid_data[FEATURE_COLUMNS], valid_data["label"])],
        eval_metric="binary_logloss",
        callbacks=[early_stopping(EARLY_STOPPING_ROUNDS, verbose=False), log_evaluation(0)],
    )

    valid_raw = eval_model.predict_proba(valid_data[FEATURE_COLUMNS])[:, 1]
    auc = float(roc_auc_score(valid_data["label"], valid_raw))
    logloss = float(log_loss(valid_data["label"], valid_raw, labels=[0, 1]))
    best_iteration = eval_model.best_iteration_

    # 確率較正(等張回帰): 検証フォールドの「予測確率→実際の1着率」を学習し、
    # 期待値計算(スコア×オッズ)に使える素直な確率へ補正する。単調変換なので
    # AI順位(レース内の並び)は変えず、確率の絶対値だけを較正する。
    calibrator = IsotonicRegression(out_of_bounds="clip")
    calibrator.fit(valid_raw, valid_data["label"].to_numpy())

    logger.info(
        "time-split eval: AUC=%.4f logloss=%.4f best_iter=%s (valid races=%d)",
        auc,
        logloss,
        best_iteration,
        len(valid_frames),
    )
    return auc, logloss, best_iteration, calibrator, len(valid_frames)


def build_model_bundle(frames: list[pd.DataFrame]) -> tuple[dict | None, dict]:
    """frames から学習済みモデル一式(bundle)をメモリ上で組み立てて返す(保存はしない)。

    学習・確率較正の手順を train_model とバックテストで共有するための関数。
    戻り値は (bundle または None, 指標dict)。1着が1件も無い等で学習不能なら bundle=None。
    """
    data = _prepare_training_data(frames)
    metrics: dict = {
        "rows": len(data),
        "auc": None,
        "logloss": None,
        "n_estimators": None,
        "valid_races": 0,
        "calibrated": False,
    }
    if data["label"].nunique() < 2:
        return None, metrics

    auc, logloss, best_iteration, calibrator, valid_races = _evaluate_with_time_split(frames)

    # 最終モデルは全データで学習する。木の本数は時系列検証で得た最適値を使い、
    # 過学習を抑える(検証できなかった場合は既定値)。
    n_estimators = best_iteration if best_iteration else DEFAULT_BOOST_ROUNDS
    model = LGBMClassifier(objective="binary", n_estimators=n_estimators, random_state=42)
    model.fit(data[FEATURE_COLUMNS], data["label"], categorical_feature=CATEGORICAL_FEATURES)

    bundle = {
        "model": model,
        "feature_columns": FEATURE_COLUMNS,
        "categorical_features": CATEGORICAL_FEATURES,
        "calibrator": calibrator,
        "version": datetime.now().strftime("KB%Y%m%d-%H%M%S"),
    }
    metrics.update(
        {
            "auc": auc,
            "logloss": logloss,
            "n_estimators": n_estimators,
            "valid_races": valid_races,
            "calibrated": calibrator is not None,
        }
    )
    return bundle, metrics


def _feature_importances(bundle: dict) -> list[dict]:
    model = bundle["model"]
    importances = getattr(model, "feature_importances_", None)
    if importances is None:
        return []
    rows = [
        {"name": name, "importance": int(value)}
        for name, value in zip(bundle["feature_columns"], importances)
    ]
    return sorted(rows, key=lambda row: row["importance"], reverse=True)


def _save_model_version(bundle: dict, metrics: dict, race_count: int) -> None:
    session = get_session()
    try:
        row = session.get(ModelVersion, bundle["version"])
        if row is None:
            row = ModelVersion(version=bundle["version"])
            session.add(row)
        version_timestamp = (
            bundle["version"][2:] if bundle["version"].startswith("KB") else bundle["version"]
        )
        row.trained_at = datetime.strptime(version_timestamp, "%Y%m%d-%H%M%S")
        row.race_count = race_count
        row.row_count = metrics["rows"]
        row.valid_race_count = metrics["valid_races"]
        row.auc = metrics["auc"]
        row.logloss = metrics["logloss"]
        row.n_estimators = metrics["n_estimators"]
        row.calibrated = metrics["calibrated"]
        row.feature_columns = json.dumps(bundle["feature_columns"], ensure_ascii=False)
        row.categorical_features = json.dumps(bundle["categorical_features"], ensure_ascii=False)
        row.feature_importances = json.dumps(_feature_importances(bundle), ensure_ascii=False)
        row.metrics = json.dumps(metrics, ensure_ascii=False)
        row.training_params = json.dumps(
            {
                "objective": "binary",
                "valid_fraction": VALID_FRACTION,
                "early_stopping_rounds": EARLY_STOPPING_ROUNDS,
                "max_boost_rounds": MAX_BOOST_ROUNDS,
                "default_boost_rounds": DEFAULT_BOOST_ROUNDS,
                "random_state": 42,
            },
            ensure_ascii=False,
        )
        row.model_path = str(MODEL_PATH)
        session.commit()
    finally:
        session.close()


def train_model() -> str:
    """モデルを学習・保存し、結果の要約を返す(WebUIのジョブ実行からも呼ばれる)。"""
    frames, race_count = _load_training_frames()

    if race_count < MIN_RACES:
        return f"学習データ不足(レース数={race_count}, 必要数={MIN_RACES})のためスキップしました"

    bundle, metrics = build_model_bundle(frames)
    if bundle is None:
        return "学習データに1着の記録が無いためスキップしました"

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, MODEL_PATH)
    _save_model_version(bundle, metrics, race_count)

    summary = (
        f"モデルを保存しました(version={bundle['version']}, レース={race_count}件, "
        f"行数={metrics['rows']}, 木の本数={metrics['n_estimators']}"
    )
    if metrics["auc"] is not None:
        summary += (
            f", 検証AUC={metrics['auc']:.4f}, 検証logloss={metrics['logloss']:.4f}, "
            f"検証レース={metrics['valid_races']}件"
        )
        summary += ", 確率較正=有効" if metrics["calibrated"] else ""
    return summary + ")"


def main() -> None:
    init_db()
    logger.info(train_model())


if __name__ == "__main__":
    main()
