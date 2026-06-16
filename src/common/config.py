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
    COLLECT_HORSES_INTERVAL_MINUTES: int = int(
        os.environ.get("COLLECT_HORSES_INTERVAL_MINUTES", "43200")
    )
    TRAIN_INTERVAL_MINUTES: int = int(os.environ.get("TRAIN_INTERVAL_MINUTES", "43200"))

    # 何日先のレースまで収集するか。JRAは主に土日開催のため、平日でも
    # 数日先を見ないと「取得レース=0件」になる(0=当日のみ)
    COLLECT_DAYS_AHEAD: int = int(os.environ.get("COLLECT_DAYS_AHEAD", "3"))

    # 賭け対象決定を行うのは発走何分前までか。AI予想はオッズ不要で未確定レース全体を対象にし、
    # オッズを使う賭け対象決定だけを発走が近いレースに限定する。
    BET_DECISION_WINDOW_MINUTES: int = int(os.environ.get("BET_DECISION_WINDOW_MINUTES", "60"))
    BET_DECISION_LEAD_MINUTES: int = int(os.environ.get("BET_DECISION_LEAD_MINUTES", "10"))
    SETTLE_DELAY_MINUTES: int = int(os.environ.get("SETTLE_DELAY_MINUTES", "20"))

    # 賭け戦略
    BET_AMOUNT: float = float(os.environ.get("BET_AMOUNT", "100"))
    BET_SCORE_THRESHOLD: float = float(os.environ.get("BET_SCORE_THRESHOLD", "0.15"))
    BET_MIN_EXPECTED_VALUE: float = float(os.environ.get("BET_MIN_EXPECTED_VALUE", "1.0"))

    # スクレイピング
    SCRAPER_REQUEST_INTERVAL_SECONDS: float = float(
        os.environ.get("SCRAPER_REQUEST_INTERVAL_SECONDS", "1")
    )

    # 馬の過去成績(horse_results)収集。netkeibaへの負荷を抑えるため1回の収集で
    # 取りに行く馬数を制限し、定期収集の度に少しずつ埋める。REFRESH_DAYS日より
    # 古い取得済みの馬は最新走を取り込むため再取得する。
    HORSE_RESULTS_PER_RUN: int = int(os.environ.get("HORSE_RESULTS_PER_RUN", "30"))
    HORSE_RESULTS_REFRESH_DAYS: int = int(os.environ.get("HORSE_RESULTS_REFRESH_DAYS", "30"))
    # 成績収集はraces起点で駆動する。1回の収集で処理する未取得レース数の上限。
    RESULTS_RACES_PER_RUN: int = int(os.environ.get("RESULTS_RACES_PER_RUN", "50"))
    # 馬の血統を何代まで収集するか(5代血統表)。
    HORSE_PEDIGREE_GENERATIONS: int = int(os.environ.get("HORSE_PEDIGREE_GENERATIONS", "5"))

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
