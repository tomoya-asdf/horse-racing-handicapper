"""app_settings(DB)と .env 既定値のマージ・読み書き。"""

import json
import logging

from src.common.db import session_scope
from src.common.models import AppSetting, ModelVersion

from .configs import BettingConfig, ModelConfig, ScheduledJobConfig
from .defaults import EDITABLE_KEYS, EVENT_CHECK_INTERVAL_MINUTES, _env_defaults, schedule_def
from .parsing import parse_exact_time, parse_setting, weekdays_from_str

logger = logging.getLogger(__name__)


def _load_overrides() -> dict[str, object]:
    with session_scope() as session:
        rows = session.query(AppSetting).filter(AppSetting.key.in_(EDITABLE_KEYS)).all()
        raw = {row.key: row.value for row in rows}

    overrides: dict[str, object] = {}
    for key, value in raw.items():
        try:
            overrides[key] = parse_setting(key, value)
        except ValueError:
            logger.warning("invalid app_settings value; falling back to .env: %s=%r", key, value)
    return overrides


def merged_settings() -> dict[str, object]:
    merged = _env_defaults()
    merged.update(_load_overrides())
    return merged


def load_betting_config() -> BettingConfig:
    merged = merged_settings()
    return BettingConfig(
        mode=str(merged["betting_mode"]),
        amount=float(merged["bet_amount"]),
        score_threshold=float(merged["bet_score_threshold"]),
        min_expected_value=float(merged["bet_min_expected_value"]),
    )


def enabled_features_from(value: object) -> list[str]:
    return [part.strip() for part in str(value).split(",") if part.strip() != ""]


def load_model_config() -> ModelConfig:
    merged = merged_settings()
    enabled = enabled_features_from(merged["model_enabled_features"])
    return ModelConfig(
        learning_rate=float(merged["model_learning_rate"]),
        num_leaves=int(merged["model_num_leaves"]),
        max_depth=int(merged["model_max_depth"]),
        min_child_samples=int(merged["model_min_child_samples"]),
        reg_alpha=float(merged["model_reg_alpha"]),
        reg_lambda=float(merged["model_reg_lambda"]),
        feature_fraction=float(merged["model_feature_fraction"]),
        bagging_fraction=float(merged["model_bagging_fraction"]),
        max_boost_rounds=int(merged["model_max_boost_rounds"]),
        early_stopping_rounds=int(merged["model_early_stopping_rounds"]),
        valid_fraction=float(merged["model_valid_fraction"]),
        min_races=int(merged["model_min_races"]),
        enabled_features=tuple(enabled),
        train_start=str(merged.get("model_train_start_date") or "") or None,
        train_end=str(merged.get("model_train_end_date") or "") or None,
    )


def latest_feature_missing_rates() -> dict:
    """最新学習モデルの特徴量欠損率マップを返す(設定画面の特徴量一覧に併記する)。

    pandas 非依存。保存済みの metrics(JSON)から読むだけなのでAPIイメージでも動く。
    """
    with session_scope() as session:
        row = (
            session.query(ModelVersion.metrics)
            .order_by(ModelVersion.trained_at.desc().nullslast(), ModelVersion.version.desc())
            .first()
        )
    if not row or not row[0]:
        return {}
    try:
        metrics = json.loads(row[0])
    except (TypeError, ValueError):
        return {}
    rates = metrics.get("feature_missing_rates")
    return rates if isinstance(rates, dict) else {}


def load_scheduled_job_config(job_name: str) -> ScheduledJobConfig | None:
    item = schedule_def(job_name)
    if item is None:
        return None
    merged = merged_settings()
    exact_time = parse_exact_time(str(item["time_key"]), merged.get(item["time_key"]))
    return ScheduledJobConfig(
        job_name=job_name,
        enabled=bool(merged[item["enabled_key"]]),
        interval_minutes=(
            None
            if exact_time
            else int(merged[item["interval_key"]])
            if item.get("interval_key") and merged.get(item["interval_key"]) is not None
            else EVENT_CHECK_INTERVAL_MINUTES
            if not item.get("interval_key") and not exact_time
            else None
        ),
        before_start_minutes=(
            None
            if exact_time
            else int(merged[item["before_key"]])
            if item.get("before_key") and merged.get(item["before_key"]) is not None
            else None
        ),
        after_start_minutes=(
            None
            if exact_time
            else int(merged[item["after_key"]])
            if item.get("after_key") and merged.get(item["after_key"]) is not None
            else None
        ),
        exact_time=exact_time,
        weekdays=weekdays_from_str(merged[item["days_key"]]),
    )


def save_settings(values: dict[str, object]) -> dict:
    unknown = set(values) - set(EDITABLE_KEYS)
    if unknown:
        raise ValueError(f"変更できない設定キーです: {', '.join(sorted(unknown))}")

    validated = {key: parse_setting(key, value) for key, value in values.items()}

    start = validated.get("model_train_start_date")
    end = validated.get("model_train_end_date")
    if start and end and str(start) > str(end):
        raise ValueError("学習期間は開始日 ≦ 終了日 で指定してください。")

    with session_scope() as session:
        for key, value in validated.items():
            stored_value = "" if value is None else str(value)
            row = session.get(AppSetting, key)
            if row is None:
                row = AppSetting(key=key, value=stored_value)
                session.add(row)
            else:
                row.value = stored_value
        session.commit()

    # 循環import回避のため、ビュー生成はここで遅延import する
    from .views import get_settings_view

    return get_settings_view()
