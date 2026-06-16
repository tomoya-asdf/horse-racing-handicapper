"""Runtime-editable settings stored in app_settings.

.env values are defaults. Values saved from the WebUI override those defaults
without requiring a container restart.
"""

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from src.common.config import settings
from src.common.db import get_session
from src.common.feature_catalog import (
    DEFAULT_ENABLED_FEATURES,
    feature_catalog,
    normalize_enabled_features,
    resolve_features,
)
from src.common.models import (
    AppSetting,
    Bet,
    BetStatus,
    JobRun,
    JobTrigger,
    ModelVersion,
    Race,
)
from src.common.timeutils import now_jst

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BettingConfig:
    mode: str
    amount: float
    score_threshold: float
    min_expected_value: float


@dataclass(frozen=True)
class ModelConfig:
    """WebUIから編集できる学習設定(特徴量の選択 + LightGBMのハイパーパラメータ)。"""

    learning_rate: float
    num_leaves: int
    max_depth: int
    min_child_samples: int
    reg_alpha: float
    reg_lambda: float
    feature_fraction: float
    bagging_fraction: float
    max_boost_rounds: int
    early_stopping_rounds: int
    valid_fraction: float
    min_races: int
    enabled_features: tuple[str, ...]
    # 学習に使うレースの期間("YYYY-MM-DD" or None=全期間)
    train_start: str | None = None
    train_end: str | None = None


@dataclass(frozen=True)
class ScheduledJobConfig:
    job_name: str
    enabled: bool
    interval_minutes: int | None = None
    before_start_minutes: int | None = None
    after_start_minutes: int | None = None
    exact_time: str | None = None
    weekdays: frozenset[int] = frozenset(range(7))


EVENT_CHECK_INTERVAL_MINUTES = 1

# 曜日はPythonのdate.weekday()に合わせ、月=0 〜 日=6 とする
ALL_WEEKDAYS = "0,1,2,3,4,5,6"


SCHEDULED_JOB_DEFS = (
    {
        "job_name": "collect",
        "enabled_key": "schedule_collect_enabled",
        "interval_key": "schedule_collect_interval_minutes",
        "time_key": "schedule_collect_time",
        "days_key": "schedule_collect_days",
        "label": "データ収集",
        "description": "レース、出馬表、単勝オッズ、結果を更新します。",
        "default_interval": settings.COLLECT_INTERVAL_MINUTES,
    },
    {
        "job_name": "collect_horses",
        "enabled_key": "schedule_collect_horses_enabled",
        "interval_key": "schedule_collect_horses_interval_minutes",
        "time_key": "schedule_collect_horses_time",
        "days_key": "schedule_collect_horses_days",
        "label": "馬過去戦績収集",
        "description": "出走馬の過去戦績と血統をまとめて補完します。",
        "default_interval": settings.COLLECT_HORSES_INTERVAL_MINUTES,
    },
    {
        "job_name": "collect_jockeys",
        "enabled_key": "schedule_collect_jockeys_enabled",
        "interval_key": "schedule_collect_jockeys_interval_minutes",
        "time_key": "schedule_collect_jockeys_time",
        "days_key": "schedule_collect_jockeys_days",
        "label": "騎手過去戦績収集",
        "description": "出走騎手の過去戦績をまとめて補完します。",
        "default_interval": settings.COLLECT_JOCKEYS_INTERVAL_MINUTES,
    },
    {
        "job_name": "collect_trainers",
        "enabled_key": "schedule_collect_trainers_enabled",
        "interval_key": "schedule_collect_trainers_interval_minutes",
        "time_key": "schedule_collect_trainers_time",
        "days_key": "schedule_collect_trainers_days",
        "label": "調教師過去戦績収集",
        "description": "出走馬の調教師の過去戦績をまとめて補完します。",
        "default_interval": settings.COLLECT_TRAINERS_INTERVAL_MINUTES,
    },
    {
        "job_name": "predict",
        "enabled_key": "schedule_predict_enabled",
        "interval_key": "schedule_predict_interval_minutes",
        "time_key": "schedule_predict_time",
        "days_key": "schedule_predict_days",
        "label": "AI予想",
        "description": "未確定レースに予測スコアを保存します。",
        "default_interval": settings.PREDICT_INTERVAL_MINUTES,
    },
    {
        "job_name": "bet_decide",
        "enabled_key": "schedule_bet_decide_enabled",
        "before_key": "schedule_bet_decide_before_start_minutes",
        "time_key": "schedule_bet_decide_time",
        "days_key": "schedule_bet_decide_days",
        "label": "賭け対象決定",
        "description": "次の発走時刻を基準に、指定分前に最新オッズで判定します。",
        "default_before": settings.BET_DECISION_LEAD_MINUTES,
    },
    {
        "job_name": "settle",
        "enabled_key": "schedule_settle_enabled",
        "after_key": "schedule_settle_after_start_minutes",
        "time_key": "schedule_settle_time",
        "days_key": "schedule_settle_days",
        "label": "決済",
        "description": "購入済みレースの発走時刻を基準に、指定分後から払戻を確認します。",
        "default_after": settings.SETTLE_DELAY_MINUTES,
    },
    {
        "job_name": "train",
        "enabled_key": "schedule_train_enabled",
        "interval_key": "schedule_train_interval_minutes",
        "time_key": "schedule_train_time",
        "days_key": "schedule_train_days",
        "label": "モデル学習",
        "description": "確定済みレースから予測モデルを再学習します。",
        "default_interval": settings.TRAIN_INTERVAL_MINUTES,
    },
)


def _schedule_defaults() -> dict[str, object]:
    defaults: dict[str, object] = {}
    for item in SCHEDULED_JOB_DEFS:
        defaults[str(item["enabled_key"])] = True
        if item.get("interval_key"):
            defaults[str(item["interval_key"])] = int(item["default_interval"])
        if item.get("before_key"):
            defaults[str(item["before_key"])] = int(item["default_before"])
        if item.get("after_key"):
            defaults[str(item["after_key"])] = int(item["default_after"])
        if item.get("time_key"):
            defaults[str(item["time_key"])] = ""
        if item.get("days_key"):
            defaults[str(item["days_key"])] = ALL_WEEKDAYS
    return defaults


# 学習設定の既定値(LightGBMの既定 + これまでハードコードしていた train.py の定数)
MODEL_SETTING_DEFAULTS: dict[str, object] = {
    "model_learning_rate": 0.1,
    "model_num_leaves": 31,
    "model_max_depth": -1,
    "model_min_child_samples": 20,
    "model_reg_alpha": 0.0,
    "model_reg_lambda": 0.0,
    "model_feature_fraction": 1.0,
    "model_bagging_fraction": 1.0,
    "model_max_boost_rounds": 1000,
    "model_early_stopping_rounds": 50,
    "model_valid_fraction": 0.2,
    "model_min_races": 20,
    "model_enabled_features": ",".join(DEFAULT_ENABLED_FEATURES),
    "model_train_start_date": "",
    "model_train_end_date": "",
}

# 検証付きの整数キー / 小数キー(model_enabled_features は別扱い)
_MODEL_INT_KEYS = {
    "model_num_leaves",
    "model_max_depth",
    "model_min_child_samples",
    "model_max_boost_rounds",
    "model_early_stopping_rounds",
    "model_min_races",
}
_MODEL_FLOAT_KEYS = {
    "model_learning_rate",
    "model_reg_alpha",
    "model_reg_lambda",
    "model_feature_fraction",
    "model_bagging_fraction",
    "model_valid_fraction",
}


def _env_defaults() -> dict[str, object]:
    defaults = {
        "betting_mode": settings.BETTING_MODE,
        "bet_amount": settings.BET_AMOUNT,
        "bet_score_threshold": settings.BET_SCORE_THRESHOLD,
        "bet_min_expected_value": settings.BET_MIN_EXPECTED_VALUE,
    }
    defaults.update(_schedule_defaults())
    defaults.update(MODEL_SETTING_DEFAULTS)
    return defaults


EDITABLE_KEYS = tuple(_env_defaults().keys())


def _parse_bool(key: str, value: object) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{key} は true/false を指定してください: {value!r}")


def _parse_number(key: str, value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} は数値を指定してください: {value!r}")


def _parse_weekdays(key: str, value: object) -> str:
    """曜日指定を ``"0,1,5"`` 形式の正規化文字列にする。

    WebUIからは配列、app_settingsからはカンマ区切り文字列で渡る。曜日番号は
    月=0〜日=6。空(どの曜日も選ばない)も許可し、その場合ジョブは実行されない。
    """
    if isinstance(value, (list, tuple)):
        raw = list(value)
    else:
        raw = [part for part in str(value).split(",") if part.strip() != ""]
    days: set[int] = set()
    for part in raw:
        try:
            day = int(str(part).strip())
        except (TypeError, ValueError):
            raise ValueError(f"{key} は0〜6の曜日番号で指定してください: {value!r}")
        if not 0 <= day <= 6:
            raise ValueError(f"{key} は0〜6の曜日番号で指定してください: {value!r}")
        days.add(day)
    return ",".join(str(day) for day in sorted(days))


def _parse_exact_time(key: str, value: object) -> str | None:
    text = str(value or "").strip()
    if text == "":
        return None
    try:
        hour_text, minute_text = text.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (TypeError, ValueError):
        raise ValueError(f"{key} は HH:MM 形式で指定してください: {value!r}")
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"{key} は HH:MM 形式で指定してください: {value!r}")
    return f"{hour:02d}:{minute:02d}"


def _weekdays_from_str(value: object) -> frozenset[int]:
    return frozenset(
        int(part) for part in str(value).split(",") if part.strip() != ""
    )


def _parse_enabled_features(key: str, value: object) -> str:
    """特徴量の選択を ``"horse_number,age,..."`` 形式の正規化文字列にする。

    WebUIからは配列、app_settingsからはカンマ区切り文字列で渡る。既知の特徴量名のみを
    FEATURE_COLUMNS の並び順で残す。空(全て無効)も許可し、その場合 load_model_config 側で
    全特徴量にフォールバックする。
    """
    if isinstance(value, (list, tuple)):
        raw = [str(part).strip() for part in value]
    else:
        raw = [part.strip() for part in str(value).split(",") if part.strip() != ""]
    return ",".join(normalize_enabled_features(raw))


def _parse_date_setting(key: str, value: object) -> str:
    """学習期間の日付を "YYYY-MM-DD" に正規化する。空(=期間指定なし)も許可。"""
    text = str(value or "").strip()
    if text == "":
        return ""
    try:
        date.fromisoformat(text)
    except (TypeError, ValueError):
        raise ValueError(f"{key} は YYYY-MM-DD 形式で指定してください: {value!r}")
    return text


def _parse_model_setting(key: str, value: object):
    if key == "model_enabled_features":
        return _parse_enabled_features(key, value)
    if key in ("model_train_start_date", "model_train_end_date"):
        return _parse_date_setting(key, value)

    number = _parse_number(key, value)
    if key in _MODEL_INT_KEYS:
        ivalue = int(number)
        if key == "model_max_depth":
            if ivalue != -1 and ivalue < 1:
                raise ValueError(f"{key} は -1(無制限)または1以上で指定してください: {value!r}")
        elif key == "model_num_leaves":
            if ivalue < 2:
                raise ValueError(f"{key} は2以上で指定してください: {value!r}")
        elif ivalue < 1:
            raise ValueError(f"{key} は1以上で指定してください: {value!r}")
        return ivalue

    # 小数キー
    if key == "model_learning_rate":
        if not 0.0 < number <= 1.0:
            raise ValueError(f"{key} は0より大きく1以下で指定してください: {value!r}")
    elif key in ("model_feature_fraction", "model_bagging_fraction"):
        if not 0.0 < number <= 1.0:
            raise ValueError(f"{key} は0より大きく1以下で指定してください: {value!r}")
    elif key == "model_valid_fraction":
        if not 0.0 < number < 1.0:
            raise ValueError(f"{key} は0より大きく1未満で指定してください: {value!r}")
    elif key in ("model_reg_alpha", "model_reg_lambda"):
        if number < 0:
            raise ValueError(f"{key} は0以上で指定してください: {value!r}")
    return float(number)


def _parse(key: str, value: object):
    if key == "betting_mode":
        if value not in ("prod", "sim"):
            raise ValueError(f"betting_mode は 'prod' か 'sim' を指定してください: {value!r}")
        return value

    if key.startswith("model_"):
        return _parse_model_setting(key, value)

    if key.startswith("schedule_") and key.endswith("_enabled"):
        return _parse_bool(key, value)

    if key.startswith("schedule_") and key.endswith("_days"):
        return _parse_weekdays(key, value)

    if key.startswith("schedule_") and key.endswith("_time"):
        return _parse_exact_time(key, value)

    if key.startswith("schedule_") and (
        key.endswith("_interval_minutes")
        or key.endswith("_before_start_minutes")
        or key.endswith("_after_start_minutes")
    ):
        if str(value or "").strip() == "":
            return None
        number = int(_parse_number(key, value))
        minimum = 1 if key.endswith("_interval_minutes") or key.endswith("_before_start_minutes") else 0
        if number < minimum:
            raise ValueError(f"{key} は {minimum} 以上で指定してください: {value!r}")
        return number

    number = _parse_number(key, value)
    if key == "bet_amount":
        if number < 100 or number % 100 != 0:
            raise ValueError(f"bet_amount は100円以上・100円単位で指定してください: {value!r}")
    elif key == "bet_score_threshold":
        if not 0.0 <= number <= 1.0:
            raise ValueError(f"bet_score_threshold は0〜1で指定してください: {value!r}")
    elif key == "bet_min_expected_value":
        if number < 0:
            raise ValueError(f"bet_min_expected_value は0以上で指定してください: {value!r}")
    else:
        raise ValueError(f"未対応の設定キーです: {key}")
    return number


def _load_overrides() -> dict[str, object]:
    session = get_session()
    try:
        rows = session.query(AppSetting).filter(AppSetting.key.in_(EDITABLE_KEYS)).all()
        raw = {row.key: row.value for row in rows}
    finally:
        session.close()

    overrides: dict[str, object] = {}
    for key, value in raw.items():
        try:
            overrides[key] = _parse(key, value)
        except ValueError:
            logger.warning("invalid app_settings value; falling back to .env: %s=%r", key, value)
    return overrides


def _merged_settings() -> dict[str, object]:
    merged = _env_defaults()
    merged.update(_load_overrides())
    return merged


def load_betting_config() -> BettingConfig:
    merged = _merged_settings()
    return BettingConfig(
        mode=str(merged["betting_mode"]),
        amount=float(merged["bet_amount"]),
        score_threshold=float(merged["bet_score_threshold"]),
        min_expected_value=float(merged["bet_min_expected_value"]),
    )


def _enabled_features_from(value: object) -> list[str]:
    return [part.strip() for part in str(value).split(",") if part.strip() != ""]


def load_model_config() -> ModelConfig:
    merged = _merged_settings()
    enabled = _enabled_features_from(merged["model_enabled_features"])
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


def _latest_feature_missing_rates() -> dict:
    """最新学習モデルの特徴量欠損率マップを返す(設定画面の特徴量一覧に併記する)。

    pandas 非依存。保存済みの metrics(JSON)から読むだけなのでAPIイメージでも動く。
    """
    session = get_session()
    try:
        row = (
            session.query(ModelVersion.metrics)
            .order_by(ModelVersion.trained_at.desc().nullslast(), ModelVersion.version.desc())
            .first()
        )
    finally:
        session.close()
    if not row or not row[0]:
        return {}
    try:
        metrics = json.loads(row[0])
    except (TypeError, ValueError):
        return {}
    rates = metrics.get("feature_missing_rates")
    return rates if isinstance(rates, dict) else {}


def _schedule_def(job_name: str) -> dict | None:
    return next((item for item in SCHEDULED_JOB_DEFS if item["job_name"] == job_name), None)


def load_scheduled_job_config(job_name: str) -> ScheduledJobConfig | None:
    item = _schedule_def(job_name)
    if item is None:
        return None
    merged = _merged_settings()
    exact_time = _parse_exact_time(str(item["time_key"]), merged.get(item["time_key"]))
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
        weekdays=_weekdays_from_str(merged[item["days_key"]]),
    )


def _latest_scheduled_started_at(session, job_name: str) -> datetime | None:
    row = (
        session.query(JobRun)
        .filter(JobRun.job_name == job_name, JobRun.trigger == JobTrigger.SCHEDULED.value)
        .order_by(JobRun.started_at.desc().nullslast(), JobRun.created_at.desc())
        .first()
    )
    return row.started_at if row is not None else None


def _next_interval_run_at(session, job_name: str, interval_minutes: int) -> datetime:
    latest = _latest_scheduled_started_at(session, job_name)
    if latest is None:
        return now_jst()
    return latest + timedelta(minutes=interval_minutes)


def _next_bet_decide_run_at(session, before_start_minutes: int) -> datetime | None:
    now = now_jst()
    target = (
        session.query(Race.start_time)
        .filter(Race.start_time.isnot(None), Race.start_time > now)
        .order_by(Race.start_time)
        .first()
    )
    if target is None or target[0] is None:
        return None
    return max(now, target[0] - timedelta(minutes=before_start_minutes))


def _next_settle_run_at(session, after_start_minutes: int) -> datetime | None:
    now = now_jst()
    row = (
        session.query(Race.start_time)
        .join(Bet, Bet.race_id == Race.id)
        .filter(
            Bet.is_settled.is_(False),
            Bet.status == BetStatus.PLACED.value,
            Race.start_time.isnot(None),
        )
        .order_by(Race.start_time)
        .first()
    )
    if row is None or row[0] is None:
        return None
    return max(now, row[0] + timedelta(minutes=after_start_minutes))


def _restrict_to_weekdays(dt: datetime | None, weekdays: frozenset[int]) -> datetime | None:
    """``dt`` を実行可能な曜日に丸める。当日が対象外なら次の対象曜日の0時へ繰り上げる。"""
    if dt is None or not weekdays:
        return None
    if dt.weekday() in weekdays:
        return dt
    for offset in range(1, 8):
        candidate = dt + timedelta(days=offset)
        if candidate.weekday() in weekdays:
            return candidate.replace(hour=0, minute=0, second=0, microsecond=0)
    return None


def _next_exact_time_run_at(exact_time: str | None, weekdays: frozenset[int]) -> datetime | None:
    if not exact_time or not weekdays:
        return None
    hour, minute = (int(part) for part in exact_time.split(":", 1))
    now = now_jst()
    for offset in range(0, 8):
        candidate_date = (now + timedelta(days=offset)).date()
        if candidate_date.weekday() not in weekdays:
            continue
        candidate = datetime.combine(candidate_date, datetime.min.time()).replace(
            hour=hour,
            minute=minute,
            tzinfo=now.tzinfo,
        )
        if candidate >= now:
            return candidate
    return None


def scheduled_jobs_view() -> list[dict]:
    merged = _merged_settings()
    session = get_session()
    try:
        items = []
        for item in SCHEDULED_JOB_DEFS:
            job_name = str(item["job_name"])
            enabled = bool(merged[item["enabled_key"]])
            interval = (
                int(merged[item["interval_key"]])
                if item.get("interval_key") and merged.get(item["interval_key"]) is not None
                else None
            )
            before_start = (
                int(merged[item["before_key"]])
                if item.get("before_key") and merged.get(item["before_key"]) is not None
                else None
            )
            after_start = (
                int(merged[item["after_key"]])
                if item.get("after_key") and merged.get(item["after_key"]) is not None
                else None
            )
            exact_time = _parse_exact_time(str(item["time_key"]), merged.get(item["time_key"]))
            weekdays = _weekdays_from_str(merged[item["days_key"]])

            if exact_time:
                interval = None
                before_start = None
                after_start = None
                next_run_at = _next_exact_time_run_at(exact_time, weekdays)
            elif interval is not None:
                next_run_at = _next_interval_run_at(session, job_name, interval)
            elif before_start is not None:
                next_run_at = _next_bet_decide_run_at(session, before_start)
            elif after_start is not None:
                next_run_at = _next_settle_run_at(session, after_start)
            else:
                next_run_at = None

            if not exact_time:
                next_run_at = _restrict_to_weekdays(next_run_at, weekdays)

            items.append(
                {
                    "job_name": job_name,
                    "enabled_key": item["enabled_key"],
                    "interval_key": item.get("interval_key"),
                    "before_start_key": item.get("before_key"),
                    "after_start_key": item.get("after_key"),
                    "time_key": item.get("time_key"),
                    "days_key": item["days_key"],
                    "label": item["label"],
                    "description": item["description"],
                    "enabled": enabled,
                    "interval_minutes": interval,
                    "before_start_minutes": before_start,
                    "after_start_minutes": after_start,
                    "exact_time": exact_time,
                    "days": sorted(weekdays),
                    "next_run_at": (
                        next_run_at.isoformat() if enabled and next_run_at is not None else None
                    ),
                }
            )
        return items
    finally:
        session.close()


def get_settings_view(include_env: bool = True) -> dict:
    config = load_betting_config()
    merged = _merged_settings()
    env_settings = [
        {"key": "POSTGRES_USER", "label": "PostgreSQLユーザー", "value": settings.POSTGRES_USER},
        {
            "key": "POSTGRES_PASSWORD",
            "label": "PostgreSQLパスワード",
            "value": "設定済み" if settings.POSTGRES_PASSWORD else "未設定",
            "secret": True,
        },
        {"key": "POSTGRES_DB", "label": "PostgreSQL DB名", "value": settings.POSTGRES_DB},
        {
            "key": "DATABASE_URL",
            "label": "DB接続URL",
            "value": "設定済み" if settings.DATABASE_URL else "未設定",
            "secret": True,
        },
        {"key": "BETTING_MODE", "label": "賭けモード", "value": settings.BETTING_MODE},
        {"key": "COLLECT_INTERVAL_MINUTES", "label": "データ収集間隔(分)", "value": settings.COLLECT_INTERVAL_MINUTES},
        {"key": "PREDICT_INTERVAL_MINUTES", "label": "AI予想間隔(分)", "value": settings.PREDICT_INTERVAL_MINUTES},
        {
            "key": "COLLECT_HORSES_INTERVAL_MINUTES",
            "label": "馬過去戦績収集間隔(分)",
            "value": settings.COLLECT_HORSES_INTERVAL_MINUTES,
        },
        {
            "key": "COLLECT_JOCKEYS_INTERVAL_MINUTES",
            "label": "騎手過去戦績収集間隔(分)",
            "value": settings.COLLECT_JOCKEYS_INTERVAL_MINUTES,
        },
        {
            "key": "COLLECT_TRAINERS_INTERVAL_MINUTES",
            "label": "調教師過去戦績収集間隔(分)",
            "value": settings.COLLECT_TRAINERS_INTERVAL_MINUTES,
        },
        {"key": "TRAIN_INTERVAL_MINUTES", "label": "モデル学習間隔(分)", "value": settings.TRAIN_INTERVAL_MINUTES},
        {"key": "BET_DECISION_LEAD_MINUTES", "label": "賭け対象決定の発走何分前", "value": settings.BET_DECISION_LEAD_MINUTES},
        {"key": "SETTLE_DELAY_MINUTES", "label": "決済確認の発走何分後", "value": settings.SETTLE_DELAY_MINUTES},
        {"key": "COLLECT_DAYS_AHEAD", "label": "先何日分まで収集", "value": settings.COLLECT_DAYS_AHEAD},
        {"key": "BET_DECISION_WINDOW_MINUTES", "label": "賭け対象決定の発走分前", "value": settings.BET_DECISION_WINDOW_MINUTES},
        {"key": "BET_AMOUNT", "label": "1件あたり賭け金(円)", "value": settings.BET_AMOUNT},
        {"key": "BET_SCORE_THRESHOLD", "label": "賭けるAIスコア下限", "value": settings.BET_SCORE_THRESHOLD},
        {"key": "BET_MIN_EXPECTED_VALUE", "label": "賭ける期待値下限", "value": settings.BET_MIN_EXPECTED_VALUE},
        {
            "key": "SCRAPER_REQUEST_INTERVAL_SECONDS",
            "label": "スクレイピング間隔(秒)",
            "value": settings.SCRAPER_REQUEST_INTERVAL_SECONDS,
        },
        {"key": "HORSE_RESULTS_PER_RUN", "label": "1回の収集で取得する馬数", "value": settings.HORSE_RESULTS_PER_RUN},
        {
            "key": "HORSE_RESULTS_REFRESH_DAYS",
            "label": "馬過去戦績の再取得間隔(日)",
            "value": settings.HORSE_RESULTS_REFRESH_DAYS,
        },
        {
            "key": "JOCKEY_RESULTS_PER_RUN",
            "label": "1回の収集で取得する騎手数",
            "value": settings.JOCKEY_RESULTS_PER_RUN,
        },
        {
            "key": "JOCKEY_RESULTS_REFRESH_DAYS",
            "label": "騎手過去戦績の再取得間隔(日)",
            "value": settings.JOCKEY_RESULTS_REFRESH_DAYS,
        },
        {
            "key": "TRAINER_RESULTS_PER_RUN",
            "label": "1回の収集で取得する調教師数",
            "value": settings.TRAINER_RESULTS_PER_RUN,
        },
        {
            "key": "TRAINER_RESULTS_REFRESH_DAYS",
            "label": "調教師過去戦績の再取得間隔(日)",
            "value": settings.TRAINER_RESULTS_REFRESH_DAYS,
        },
        {
            "key": "IPAT_SUBSCRIBER_NUMBER",
            "label": "IPAT加入者番号",
            "value": "設定済み" if settings.IPAT_SUBSCRIBER_NUMBER else "未設定",
            "secret": True,
        },
        {
            "key": "IPAT_PIN",
            "label": "IPAT暗証番号",
            "value": "設定済み" if settings.IPAT_PIN else "未設定",
            "secret": True,
        },
        {
            "key": "IPAT_PARS_NUMBER",
            "label": "IPAT P-ARS番号",
            "value": "設定済み" if settings.IPAT_PARS_NUMBER else "未設定",
            "secret": True,
        },
        {"key": "IPAT_DRY_RUN", "label": "IPATドライラン", "value": settings.IPAT_DRY_RUN},
        {
            "key": "ADMIN_LOGIN_ID",
            "label": "管理ログインID",
            "value": "設定済み" if settings.ADMIN_LOGIN_ID else "未設定",
            "secret": True,
        },
        {
            "key": "ADMIN_PASSWORD",
            "label": "管理パスワード",
            "value": "設定済み" if settings.ADMIN_PASSWORD else "未設定",
            "secret": True,
        },
    ]
    selected_features, _ = resolve_features(
        _enabled_features_from(merged["model_enabled_features"])
    )
    missing_rates = _latest_feature_missing_rates()
    return {
        "editable": {
            key: merged[key]
            for key in EDITABLE_KEYS
            if key.startswith("schedule_")
            or key.startswith("model_")
            or key
            in (
                "betting_mode",
                "bet_amount",
                "bet_score_threshold",
                "bet_min_expected_value",
            )
        },
        "model_features": feature_catalog(selected_features, missing_rates),
        "readonly": {
            "scraper_request_interval_seconds": settings.SCRAPER_REQUEST_INTERVAL_SECONDS,
            "ipat_dry_run": settings.IPAT_DRY_RUN,
            "ipat_credentials_configured": bool(
                settings.IPAT_SUBSCRIBER_NUMBER
                and settings.IPAT_PIN
                and settings.IPAT_PARS_NUMBER
            ),
        },
        "scheduled_jobs": scheduled_jobs_view(),
        "env_settings": env_settings if include_env else [],
    }


def save_settings(values: dict[str, object]) -> dict:
    unknown = set(values) - set(EDITABLE_KEYS)
    if unknown:
        raise ValueError(f"変更できない設定キーです: {', '.join(sorted(unknown))}")

    validated = {key: _parse(key, value) for key, value in values.items()}

    start = validated.get("model_train_start_date")
    end = validated.get("model_train_end_date")
    if start and end and str(start) > str(end):
        raise ValueError("学習期間は開始日 ≦ 終了日 で指定してください。")

    session = get_session()
    try:
        for key, value in validated.items():
            stored_value = "" if value is None else str(value)
            row = session.get(AppSetting, key)
            if row is None:
                row = AppSetting(key=key, value=stored_value)
                session.add(row)
            else:
                row.value = stored_value
        session.commit()
    finally:
        session.close()
    return get_settings_view()
