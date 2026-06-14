"""Runtime-editable settings stored in app_settings.

.env values are defaults. Values saved from the WebUI override those defaults
without requiring a container restart.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.common.config import settings
from src.common.db import get_session
from src.common.models import AppSetting, Bet, BetStatus, JobRun, JobTrigger, Race
from src.common.timeutils import now_jst

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BettingConfig:
    mode: str
    amount: float
    score_threshold: float
    min_expected_value: float


@dataclass(frozen=True)
class ScheduledJobConfig:
    job_name: str
    enabled: bool
    interval_minutes: int | None = None
    before_start_minutes: int | None = None
    after_start_minutes: int | None = None
    weekdays: frozenset[int] = frozenset(range(7))


EVENT_CHECK_INTERVAL_MINUTES = 1

# 曜日はPythonのdate.weekday()に合わせ、月=0 〜 日=6 とする
ALL_WEEKDAYS = "0,1,2,3,4,5,6"


SCHEDULED_JOB_DEFS = (
    {
        "job_name": "collect",
        "enabled_key": "schedule_collect_enabled",
        "interval_key": "schedule_collect_interval_minutes",
        "days_key": "schedule_collect_days",
        "label": "データ収集",
        "description": "レース、出馬表、単勝オッズ、結果を更新します。",
        "default_interval": settings.COLLECT_INTERVAL_MINUTES,
    },
    {
        "job_name": "collect_horses",
        "enabled_key": "schedule_collect_horses_enabled",
        "interval_key": "schedule_collect_horses_interval_minutes",
        "days_key": "schedule_collect_horses_days",
        "label": "馬過去戦績収集",
        "description": "出走馬の過去戦績と血統をまとめて補完します。",
        "default_interval": settings.COLLECT_HORSES_INTERVAL_MINUTES,
    },
    {
        "job_name": "collect_jockeys",
        "enabled_key": "schedule_collect_jockeys_enabled",
        "interval_key": "schedule_collect_jockeys_interval_minutes",
        "days_key": "schedule_collect_jockeys_days",
        "label": "騎手過去戦績収集",
        "description": "出走騎手の過去戦績をまとめて補完します。",
        "default_interval": settings.COLLECT_JOCKEYS_INTERVAL_MINUTES,
    },
    {
        "job_name": "collect_trainers",
        "enabled_key": "schedule_collect_trainers_enabled",
        "interval_key": "schedule_collect_trainers_interval_minutes",
        "days_key": "schedule_collect_trainers_days",
        "label": "調教師過去戦績収集",
        "description": "出走馬の調教師の過去戦績をまとめて補完します。",
        "default_interval": settings.COLLECT_TRAINERS_INTERVAL_MINUTES,
    },
    {
        "job_name": "predict",
        "enabled_key": "schedule_predict_enabled",
        "interval_key": "schedule_predict_interval_minutes",
        "days_key": "schedule_predict_days",
        "label": "AI予想",
        "description": "未確定レースに予測スコアを保存します。",
        "default_interval": settings.PREDICT_INTERVAL_MINUTES,
    },
    {
        "job_name": "bet_decide",
        "enabled_key": "schedule_bet_decide_enabled",
        "before_key": "schedule_bet_decide_before_start_minutes",
        "days_key": "schedule_bet_decide_days",
        "label": "賭け対象決定",
        "description": "次の発走時刻を基準に、指定分前に最新オッズで判定します。",
        "default_before": settings.BET_DECISION_LEAD_MINUTES,
    },
    {
        "job_name": "settle",
        "enabled_key": "schedule_settle_enabled",
        "after_key": "schedule_settle_after_start_minutes",
        "days_key": "schedule_settle_days",
        "label": "決済",
        "description": "購入済みレースの発走時刻を基準に、指定分後から払戻を確認します。",
        "default_after": settings.SETTLE_DELAY_MINUTES,
    },
    {
        "job_name": "train",
        "enabled_key": "schedule_train_enabled",
        "interval_key": "schedule_train_interval_minutes",
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
        if item.get("days_key"):
            defaults[str(item["days_key"])] = ALL_WEEKDAYS
    return defaults


def _env_defaults() -> dict[str, object]:
    defaults = {
        "betting_mode": settings.BETTING_MODE,
        "bet_amount": settings.BET_AMOUNT,
        "bet_score_threshold": settings.BET_SCORE_THRESHOLD,
        "bet_min_expected_value": settings.BET_MIN_EXPECTED_VALUE,
    }
    defaults.update(_schedule_defaults())
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


def _weekdays_from_str(value: object) -> frozenset[int]:
    return frozenset(
        int(part) for part in str(value).split(",") if part.strip() != ""
    )


def _parse(key: str, value: object):
    if key == "betting_mode":
        if value not in ("prod", "sim"):
            raise ValueError(f"betting_mode は 'prod' か 'sim' を指定してください: {value!r}")
        return value

    if key.startswith("schedule_") and key.endswith("_enabled"):
        return _parse_bool(key, value)

    if key.startswith("schedule_") and key.endswith("_days"):
        return _parse_weekdays(key, value)

    if key.startswith("schedule_") and (
        key.endswith("_interval_minutes")
        or key.endswith("_before_start_minutes")
        or key.endswith("_after_start_minutes")
    ):
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


def _schedule_def(job_name: str) -> dict | None:
    return next((item for item in SCHEDULED_JOB_DEFS if item["job_name"] == job_name), None)


def load_scheduled_job_config(job_name: str) -> ScheduledJobConfig | None:
    item = _schedule_def(job_name)
    if item is None:
        return None
    merged = _merged_settings()
    return ScheduledJobConfig(
        job_name=job_name,
        enabled=bool(merged[item["enabled_key"]]),
        interval_minutes=(
            int(merged[item["interval_key"]]) if item.get("interval_key") else EVENT_CHECK_INTERVAL_MINUTES
        ),
        before_start_minutes=(
            int(merged[item["before_key"]]) if item.get("before_key") else None
        ),
        after_start_minutes=(
            int(merged[item["after_key"]]) if item.get("after_key") else None
        ),
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


def scheduled_jobs_view() -> list[dict]:
    merged = _merged_settings()
    session = get_session()
    try:
        items = []
        for item in SCHEDULED_JOB_DEFS:
            job_name = str(item["job_name"])
            enabled = bool(merged[item["enabled_key"]])
            interval = (
                int(merged[item["interval_key"]]) if item.get("interval_key") else None
            )
            before_start = (
                int(merged[item["before_key"]]) if item.get("before_key") else None
            )
            after_start = (
                int(merged[item["after_key"]]) if item.get("after_key") else None
            )
            weekdays = _weekdays_from_str(merged[item["days_key"]])

            if interval is not None:
                next_run_at = _next_interval_run_at(session, job_name, interval)
            elif before_start is not None:
                next_run_at = _next_bet_decide_run_at(session, before_start)
            elif after_start is not None:
                next_run_at = _next_settle_run_at(session, after_start)
            else:
                next_run_at = None

            next_run_at = _restrict_to_weekdays(next_run_at, weekdays)

            items.append(
                {
                    "job_name": job_name,
                    "enabled_key": item["enabled_key"],
                    "interval_key": item.get("interval_key"),
                    "before_start_key": item.get("before_key"),
                    "after_start_key": item.get("after_key"),
                    "days_key": item["days_key"],
                    "label": item["label"],
                    "description": item["description"],
                    "enabled": enabled,
                    "interval_minutes": interval,
                    "before_start_minutes": before_start,
                    "after_start_minutes": after_start,
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
    return {
        "editable": {
            key: merged[key]
            for key in EDITABLE_KEYS
            if key.startswith("schedule_")
            or key
            in (
                "betting_mode",
                "bet_amount",
                "bet_score_threshold",
                "bet_min_expected_value",
            )
        },
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

    session = get_session()
    try:
        for key, value in validated.items():
            row = session.get(AppSetting, key)
            if row is None:
                row = AppSetting(key=key, value=str(value))
                session.add(row)
            else:
                row.value = str(value)
        session.commit()
    finally:
        session.close()
    return get_settings_view()
