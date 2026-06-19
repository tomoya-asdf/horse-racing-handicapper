"""設定ページ用のビュー(編集可能値 + 読み取り専用の .env 値 + 特徴量一覧)。"""

from src.common.config import settings
from src.common.feature_catalog import feature_catalog, resolve_features

from .defaults import EDITABLE_KEYS
from .schedule import scheduled_jobs_view
from .store import (
    enabled_features_from,
    latest_feature_missing_rates,
    merged_settings,
)


def get_settings_view(include_env: bool = True) -> dict:
    merged = merged_settings()
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
        enabled_features_from(merged["model_enabled_features"])
    )
    missing_rates = latest_feature_missing_rates()
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
