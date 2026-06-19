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

from src.common.db import init_db, session_scope
from src.common.dynamic_config import ModelConfig, load_model_config
from src.common.models import Entry, ModelVersion, Race
from src.common.paths import MODEL_PATH
from src.predictor.features import (
    CATEGORICAL_FEATURES,
    DEFAULT_CONDITION,
    FEATURE_COLUMNS,
    build_features,
    resolve_features,
)
from src.predictor.history import (
    build_entries_frame,
    load_horse_history,
    load_jockey_history,
    load_sire_map,
    load_trainer_history,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 検証できなかった場合の最終モデルの木の本数(時系列検証で最適値が得られない時のフォールバック)
DEFAULT_BOOST_ROUNDS = 100


def _lgbm_params(config: ModelConfig, n_estimators: int) -> dict:
    """ModelConfig から LGBMClassifier に渡すパラメータ辞書を組み立てる。"""
    params = dict(
        objective="binary",
        n_estimators=n_estimators,
        learning_rate=config.learning_rate,
        num_leaves=config.num_leaves,
        max_depth=config.max_depth,
        min_child_samples=config.min_child_samples,
        reg_alpha=config.reg_alpha,
        reg_lambda=config.reg_lambda,
        colsample_bytree=config.feature_fraction,
        subsample=config.bagging_fraction,
        # subsample(bagging)はsubsample_freq>0でないと効かない。1未満の時だけ毎回サンプリングする
        subsample_freq=1 if config.bagging_fraction < 1.0 else 0,
        random_state=42,
    )
    return params


def _prepare_training_data(frames: list[pd.DataFrame]) -> pd.DataFrame:
    data = pd.concat(frames)
    for column in CATEGORICAL_FEATURES:
        data[column] = data[column].astype("category")
    return data


def load_training_frames(
    before: date | None = None,
    start: date | None = None,
    end: date | None = None,
) -> tuple[list[pd.DataFrame], int]:
    """確定済みレースの特徴量+ラベルをレース単位のDataFrameリストで返す。

    ``before`` を指定すると、その日付より前のレースに限定する
    (バックテスト時に検証期間のデータを学習から除外するため)。
    ``start`` / ``end`` は学習に使うレースの期間(両端含む)。未指定なら全期間。
    """
    with session_scope() as session:
        query = (
            session.query(Race)
            .join(Entry)
            .filter(Entry.finish_position.isnot(None))
        )
        if before is not None:
            query = query.filter(Race.race_date < before)
        if start is not None:
            query = query.filter(Race.race_date >= start)
        if end is not None:
            query = query.filter(Race.race_date <= end)
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


def _evaluate_with_time_split(
    frames: list[pd.DataFrame],
    config: ModelConfig,
    feature_columns: list[str],
    categorical_features: list[str],
) -> tuple[float | None, float | None, int | None, IsotonicRegression | None, int]:
    """時系列分割(古い側で学習・新しい側で検証)で評価する。

    戻り値は (検証AUC, 検証logloss, 最適な木の本数, 確率較正器, 検証レース数)。
    検証に使えるデータが無い/片側クラスのみの場合は AUC等を None で返す。
    """
    split = int(len(frames) * (1 - config.valid_fraction))
    train_frames = frames[:split]
    valid_frames = frames[split:]
    if not train_frames or not valid_frames:
        return None, None, None, None, 0

    train_data = _prepare_training_data(train_frames)
    valid_data = _prepare_training_data(valid_frames)
    if train_data["label"].nunique() < 2 or valid_data["label"].nunique() < 2:
        return None, None, None, None, len(valid_frames)

    eval_model = LGBMClassifier(**_lgbm_params(config, config.max_boost_rounds))
    eval_model.fit(
        train_data[feature_columns],
        train_data["label"],
        categorical_feature=categorical_features,
        eval_set=[(valid_data[feature_columns], valid_data["label"])],
        eval_metric="binary_logloss",
        callbacks=[early_stopping(config.early_stopping_rounds, verbose=False), log_evaluation(0)],
    )

    valid_raw = eval_model.predict_proba(valid_data[feature_columns])[:, 1]
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


def build_model_bundle(
    frames: list[pd.DataFrame], model_config: ModelConfig | None = None
) -> tuple[dict | None, dict]:
    """frames から学習済みモデル一式(bundle)をメモリ上で組み立てて返す(保存はしない)。

    学習・確率較正の手順を train_model とバックテストで共有するための関数。
    ``model_config`` 省略時は WebUIで設定された現行の学習設定を読み込む。
    戻り値は (bundle または None, 指標dict)。1着が1件も無い等で学習不能なら bundle=None。
    """
    config = model_config or load_model_config()
    # WebUIで選択された特徴量(と、そのうちのカテゴリ列)だけで学習する
    feature_columns, categorical_features = resolve_features(list(config.enabled_features))

    data = _prepare_training_data(frames)
    # 全特徴量の欠損率(選択に依らず全 FEATURE_COLUMNS を算出。設定画面・モデル詳細で表示する)
    missing_rates = _feature_missing_rates(data)
    metrics: dict = {
        "rows": len(data),
        "auc": None,
        "logloss": None,
        "n_estimators": None,
        "valid_races": 0,
        "calibrated": False,
        "feature_count": len(feature_columns),
        "feature_missing_rates": missing_rates,
    }
    if data["label"].nunique() < 2:
        return None, metrics

    auc, logloss, best_iteration, calibrator, valid_races = _evaluate_with_time_split(
        frames, config, feature_columns, categorical_features
    )

    # 最終モデルは全データで学習する。木の本数は時系列検証で得た最適値を使い、
    # 過学習を抑える(検証できなかった場合は既定値)。
    n_estimators = best_iteration if best_iteration else DEFAULT_BOOST_ROUNDS
    model = LGBMClassifier(**_lgbm_params(config, n_estimators))
    model.fit(data[feature_columns], data["label"], categorical_feature=categorical_features)

    bundle = {
        "model": model,
        "feature_columns": feature_columns,
        "categorical_features": categorical_features,
        "calibrator": calibrator,
        "version": datetime.now().strftime("KB%Y%m%d-%H%M%S"),
        "training_params": _training_params(config, n_estimators),
        "feature_missing_rates": missing_rates,
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


def _training_params(config: ModelConfig, n_estimators: int) -> dict:
    """学習に実際に使ったハイパーパラメータ(モデル詳細ページ・記録用)。"""
    return {
        "objective": "binary",
        "learning_rate": config.learning_rate,
        "num_leaves": config.num_leaves,
        "max_depth": config.max_depth,
        "min_child_samples": config.min_child_samples,
        "reg_alpha": config.reg_alpha,
        "reg_lambda": config.reg_lambda,
        "feature_fraction": config.feature_fraction,
        "bagging_fraction": config.bagging_fraction,
        "valid_fraction": config.valid_fraction,
        "early_stopping_rounds": config.early_stopping_rounds,
        "max_boost_rounds": config.max_boost_rounds,
        "n_estimators": n_estimators,
        "default_boost_rounds": DEFAULT_BOOST_ROUNDS,
        "random_state": 42,
    }


def _feature_missing_rates(data: pd.DataFrame) -> dict[str, float]:
    """学習データ(全 FEATURE_COLUMNS)の特徴量ごとの欠損率(0〜1)を返す。

    数値列は NaN を欠損、カテゴリ列は欠損埋めのセンチネル("unknown")一致を欠損とみなす。
    """
    rates: dict[str, float] = {}
    n = len(data)
    if n == 0:
        return rates
    categorical = set(CATEGORICAL_FEATURES)
    for column in FEATURE_COLUMNS:
        if column not in data.columns:
            continue
        series = data[column]
        if column in categorical:
            missing = int((series.astype(str) == DEFAULT_CONDITION).sum())
        else:
            missing = int(series.isna().sum())
        rates[column] = round(missing / n, 4)
    return rates


def _feature_importances(bundle: dict) -> list[dict]:
    """特徴量重要度を gain(利得)で算出して返す。

    LightGBM 既定の "split"(分岐回数)は高カードナリティなID(騎手・調教師等)が
    不当に高く出るため、寄与の大きさを表す gain を使う。各行に欠損率も付与する。
    """
    model = bundle["model"]
    feature_columns = bundle["feature_columns"]
    missing_rates = bundle.get("feature_missing_rates") or {}
    booster = getattr(model, "booster_", None)
    if booster is not None:
        importances = booster.feature_importance(importance_type="gain")
    else:
        importances = getattr(model, "feature_importances_", None)
    if importances is None:
        return []
    rows = [
        {
            "name": name,
            "importance": int(round(float(value))),
            "missing_rate": missing_rates.get(name),
        }
        for name, value in zip(feature_columns, importances)
    ]
    return sorted(rows, key=lambda row: row["importance"], reverse=True)


def _save_model_version(bundle: dict, metrics: dict, race_count: int) -> None:
    with session_scope() as session:
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
            bundle.get("training_params", {}), ensure_ascii=False
        )
        row.model_path = str(MODEL_PATH)
        session.commit()


def train_model() -> str:
    """モデルを学習・保存し、結果の要約を返す(WebUIのジョブ実行からも呼ばれる)。"""
    config = load_model_config()
    start = date.fromisoformat(config.train_start) if config.train_start else None
    end = date.fromisoformat(config.train_end) if config.train_end else None
    frames, race_count = load_training_frames(start=start, end=end)

    if race_count < config.min_races:
        return f"学習データ不足(レース数={race_count}, 必要数={config.min_races})のためスキップしました"

    bundle, metrics = build_model_bundle(frames, config)
    if bundle is None:
        return "学習データに1着の記録が無いためスキップしました"

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, MODEL_PATH)
    _save_model_version(bundle, metrics, race_count)

    period = (
        f", 期間={config.train_start or '最初'}〜{config.train_end or '最新'}"
        if (config.train_start or config.train_end)
        else ""
    )
    summary = (
        f"モデルを保存しました(version={bundle['version']}, レース={race_count}件{period}, "
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
