"""WebUIから変更可能な設定(app_settingsテーブル)と.envのマージ。

設定は2層に分かれる。

- .env (src/common/config.py): DB接続・ジョブ間隔・IPAT認証情報などの静的設定。
  変更にはコンテナの再起動が必要で、WebUIには表示のみ(認証情報は非表示)。
- app_settings: 賭け関連の設定。WebUIから変更でき、各ジョブは実行のたびに
  ``load_betting_config()`` で読み直すため再起動なしで反映される。
  値が無い・不正なキーは.envの値にフォールバックする。
"""

import logging
from dataclasses import dataclass

from src.common.config import settings
from src.common.db import get_session
from src.common.models import AppSetting

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BettingConfig:
    mode: str
    amount: float
    score_threshold: float
    min_expected_value: float


def _env_defaults() -> dict[str, object]:
    return {
        "betting_mode": settings.BETTING_MODE,
        "bet_amount": settings.BET_AMOUNT,
        "bet_score_threshold": settings.BET_SCORE_THRESHOLD,
        "bet_min_expected_value": settings.BET_MIN_EXPECTED_VALUE,
    }


def _parse(key: str, value: object):
    """設定値を検証して正規化済みの値を返す。不正な場合はValueError。"""
    if key == "betting_mode":
        if value not in ("prod", "sim"):
            raise ValueError(f"betting_mode は 'prod' か 'sim' を指定してください: {value!r}")
        return value

    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} は数値を指定してください: {value!r}")

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


EDITABLE_KEYS = ("betting_mode", "bet_amount", "bet_score_threshold", "bet_min_expected_value")


def _load_overrides() -> dict[str, object]:
    session = get_session()
    try:
        rows = (
            session.query(AppSetting).filter(AppSetting.key.in_(EDITABLE_KEYS)).all()
        )
        raw = {row.key: row.value for row in rows}
    finally:
        session.close()

    overrides: dict[str, object] = {}
    for key, value in raw.items():
        try:
            overrides[key] = _parse(key, value)
        except ValueError:
            logger.warning("app_settingsの値が不正なため.envの値を使います: %s=%r", key, value)
    return overrides


def load_betting_config() -> BettingConfig:
    merged = _env_defaults()
    merged.update(_load_overrides())
    return BettingConfig(
        mode=str(merged["betting_mode"]),
        amount=float(merged["bet_amount"]),
        score_threshold=float(merged["bet_score_threshold"]),
        min_expected_value=float(merged["bet_min_expected_value"]),
    )


def get_settings_view() -> dict:
    """WebUIの設定画面に表示する内容(変更可能な設定 + 表示のみの.env設定)。"""
    config = load_betting_config()
    env_settings = [
        {"key": "POSTGRES_USER", "label": "PostgreSQLユーザー", "value": settings.POSTGRES_USER},
        {"key": "POSTGRES_PASSWORD", "label": "PostgreSQLパスワード", "value": "設定済み" if settings.POSTGRES_PASSWORD else "未設定", "secret": True},
        {"key": "POSTGRES_DB", "label": "PostgreSQL DB名", "value": settings.POSTGRES_DB},
        {"key": "DATABASE_URL", "label": "DB接続URL", "value": "設定済み" if settings.DATABASE_URL else "未設定", "secret": True},
        {"key": "BETTING_MODE", "label": "賭けモード", "value": settings.BETTING_MODE},
        {"key": "COLLECT_INTERVAL_MINUTES", "label": "データ収集間隔(分)", "value": settings.COLLECT_INTERVAL_MINUTES},
        {"key": "PREDICT_INTERVAL_MINUTES", "label": "予測・決済間隔(分)", "value": settings.PREDICT_INTERVAL_MINUTES},
        {"key": "COLLECT_DAYS_AHEAD", "label": "先何日分まで収集", "value": settings.COLLECT_DAYS_AHEAD},
        {"key": "BET_WINDOW_MINUTES", "label": "賭け判定の発走分前", "value": settings.BET_WINDOW_MINUTES},
        {"key": "BET_AMOUNT", "label": "1件あたり賭け金(円)", "value": settings.BET_AMOUNT},
        {"key": "BET_SCORE_THRESHOLD", "label": "賭けるAIスコア下限", "value": settings.BET_SCORE_THRESHOLD},
        {"key": "BET_MIN_EXPECTED_VALUE", "label": "賭ける期待値下限", "value": settings.BET_MIN_EXPECTED_VALUE},
        {"key": "SCRAPER_REQUEST_INTERVAL_SECONDS", "label": "スクレイピング間隔(秒)", "value": settings.SCRAPER_REQUEST_INTERVAL_SECONDS},
        {"key": "IPAT_SUBSCRIBER_NUMBER", "label": "IPAT加入者番号", "value": "設定済み" if settings.IPAT_SUBSCRIBER_NUMBER else "未設定", "secret": True},
        {"key": "IPAT_PIN", "label": "IPAT暗証番号", "value": "設定済み" if settings.IPAT_PIN else "未設定", "secret": True},
        {"key": "IPAT_PARS_NUMBER", "label": "IPAT P-ARS番号", "value": "設定済み" if settings.IPAT_PARS_NUMBER else "未設定", "secret": True},
        {"key": "IPAT_DRY_RUN", "label": "IPATドライラン", "value": settings.IPAT_DRY_RUN},
    ]
    return {
        "editable": {
            "betting_mode": config.mode,
            "bet_amount": config.amount,
            "bet_score_threshold": config.score_threshold,
            "bet_min_expected_value": config.min_expected_value,
        },
        "readonly": {
            "collect_interval_minutes": settings.COLLECT_INTERVAL_MINUTES,
            "predict_interval_minutes": settings.PREDICT_INTERVAL_MINUTES,
            "scraper_request_interval_seconds": settings.SCRAPER_REQUEST_INTERVAL_SECONDS,
            "ipat_dry_run": settings.IPAT_DRY_RUN,
            "ipat_credentials_configured": bool(
                settings.IPAT_SUBSCRIBER_NUMBER
                and settings.IPAT_PIN
                and settings.IPAT_PARS_NUMBER
            ),
        },
        "env_settings": env_settings,
    }


def save_settings(values: dict[str, object]) -> dict:
    """設定を検証して保存する。1つでも不正な値があれば何も保存しない。"""
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
