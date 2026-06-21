"""動的設定のキー定義と既定値(.env を起点にした defaults)。"""

from src.common.config import settings
from src.common.feature_catalog import DEFAULT_ENABLED_FEATURES

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
        "job_name": "predict",
        "enabled_key": "schedule_predict_enabled",
        "interval_key": "schedule_predict_interval_minutes",
        "time_key": "schedule_predict_time",
        "days_key": "schedule_predict_days",
        "label": "AI予想",
        "description": "未確定レースに予測スコアを保存します。発走が近いレースは最新オッズ(複勝/馬連/ワイド含む)と馬体重を取り込んで再予測します。",
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
        "description": "発走後のレースを対象に、指定分後から着順・確定オッズの反映(未確定→確定)と購入済みレースの払戻確認を行います。",
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


def schedule_def(job_name: str) -> dict | None:
    return next((item for item in SCHEDULED_JOB_DEFS if item["job_name"] == job_name), None)
