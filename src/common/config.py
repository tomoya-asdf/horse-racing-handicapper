import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    POSTGRES_USER: str = os.environ.get("POSTGRES_USER", "horse")
    POSTGRES_PASSWORD: str = os.environ.get("POSTGRES_PASSWORD", "horse")
    POSTGRES_DB: str = os.environ.get("POSTGRES_DB", "horse_racing")
    DATABASE_URL: str = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg2://horse:horse@db:5432/horse_racing",
    )
    BETTING_MODE: str = os.environ.get("BETTING_MODE", "sim")
    COLLECT_INTERVAL_MINUTES: int = int(os.environ.get("COLLECT_INTERVAL_MINUTES", "60"))
    PREDICT_INTERVAL_MINUTES: int = int(os.environ.get("PREDICT_INTERVAL_MINUTES", "30"))

    # 何日先のレースまで収集するか。JRAは主に土日開催のため、平日でも
    # 数日先を見ないと「取得レース=0件」になる(0=当日のみ)
    COLLECT_DAYS_AHEAD: int = int(os.environ.get("COLLECT_DAYS_AHEAD", "3"))

    # 賭け判断を行うのは発走何分前から先か。レースは数日前から収集されるが、
    # オッズが古い時点で予測・賭けをしないよう、発走が近いレースに限定する
    BET_WINDOW_MINUTES: int = int(os.environ.get("BET_WINDOW_MINUTES", "60"))

    # 賭け戦略
    BET_AMOUNT: float = float(os.environ.get("BET_AMOUNT", "100"))
    BET_SCORE_THRESHOLD: float = float(os.environ.get("BET_SCORE_THRESHOLD", "0.15"))
    BET_MIN_EXPECTED_VALUE: float = float(os.environ.get("BET_MIN_EXPECTED_VALUE", "1.0"))

    # スクレイピング
    SCRAPER_REQUEST_INTERVAL_SECONDS: float = float(
        os.environ.get("SCRAPER_REQUEST_INTERVAL_SECONDS", "1")
    )

    # IPAT (JRA即時購入) 自動操作
    IPAT_SUBSCRIBER_NUMBER: str = os.environ.get("IPAT_SUBSCRIBER_NUMBER", "")
    IPAT_PIN: str = os.environ.get("IPAT_PIN", "")
    IPAT_PARS_NUMBER: str = os.environ.get("IPAT_PARS_NUMBER", "")
    IPAT_DRY_RUN: bool = os.environ.get("IPAT_DRY_RUN", "true").lower() in ("1", "true", "yes")
    ADMIN_LOGIN_ID: str = os.environ.get("ADMIN_LOGIN_ID", "")
    ADMIN_PASSWORD: str = os.environ.get("ADMIN_PASSWORD", "")


settings = Settings()

# 設定ミスは起動時に検知して落とす(特にBETTING_MODEのタイポは、prodのつもりが
# 賭けが一切実行されない・simのつもりが実購入される、といった事故につながる)
if settings.BETTING_MODE not in ("prod", "sim"):
    raise ValueError(
        f"BETTING_MODE は 'prod' か 'sim' を指定してください: {settings.BETTING_MODE!r}"
    )

if settings.BET_AMOUNT < 100 or settings.BET_AMOUNT % 100 != 0:
    raise ValueError(
        f"BET_AMOUNT は100円以上・100円単位で指定してください: {settings.BET_AMOUNT!r}"
    )
