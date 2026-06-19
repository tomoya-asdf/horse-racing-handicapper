"""環境変数(.env)から読み込む起動時設定。

pydantic-settings で型変換と範囲検証を行う。不正値は起動時に ``ValidationError``
で落とし、設定ミス(特に BETTING_MODE のタイポや負のインターバル)を早期に検知する。
WebUI から変更できる動的設定は ``dynamic_config`` 側で別管理する。
"""

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # PostgreSQL
    POSTGRES_USER: str = "horse"
    POSTGRES_PASSWORD: str = "horse"
    POSTGRES_DB: str = "horse_racing"
    DATABASE_URL: str = "postgresql+psycopg2://horse:horse@db:5432/horse_racing"

    # 接続プール(collector / predictor / api が同一DBを使う)
    DB_POOL_SIZE: int = Field(default=5, ge=1)
    DB_MAX_OVERFLOW: int = Field(default=10, ge=0)

    # 賭けモード
    BETTING_MODE: str = "sim"

    # 定期実行インターバル(分)
    COLLECT_INTERVAL_MINUTES: int = Field(default=60, ge=1)
    PREDICT_INTERVAL_MINUTES: int = Field(default=30, ge=1)
    COLLECT_HORSES_INTERVAL_MINUTES: int = Field(default=43200, ge=1)
    TRAIN_INTERVAL_MINUTES: int = Field(default=43200, ge=1)

    # 何日先のレースまで収集するか。JRAは主に土日開催のため、平日でも
    # 数日先を見ないと「取得レース=0件」になる(0=当日のみ)
    COLLECT_DAYS_AHEAD: int = Field(default=3, ge=0)

    # 発走時刻基準のジョブ。AI予想はオッズ不要で未確定レース全体を対象にし、
    # オッズを使う賭け対象決定だけを発走が近いレースに限定する。
    BET_DECISION_WINDOW_MINUTES: int = Field(default=60, ge=1)
    BET_DECISION_LEAD_MINUTES: int = Field(default=10, ge=0)
    SETTLE_DELAY_MINUTES: int = Field(default=20, ge=0)

    # 賭け戦略
    BET_AMOUNT: float = 100
    BET_SCORE_THRESHOLD: float = Field(default=0.15, ge=0.0, le=1.0)
    BET_MIN_EXPECTED_VALUE: float = Field(default=1.0, ge=0.0)

    # スクレイピング
    SCRAPER_REQUEST_INTERVAL_SECONDS: float = Field(default=1, ge=0.0)
    # netkeibaの一時的な5xx/タイムアウト時のリトライ回数とバックオフ基準秒
    SCRAPER_MAX_RETRIES: int = Field(default=3, ge=0)
    SCRAPER_RETRY_BACKOFF_SECONDS: float = Field(default=1.0, ge=0.0)

    # 馬の過去成績(horse_results)収集。netkeibaへの負荷を抑えるため1回の収集で
    # 取りに行く馬数を制限し、定期収集の度に少しずつ埋める。REFRESH_DAYS日より
    # 古い取得済みの馬は最新走を取り込むため再取得する。
    HORSE_RESULTS_PER_RUN: int = Field(default=30, ge=1)
    HORSE_RESULTS_REFRESH_DAYS: int = Field(default=30, ge=1)
    # 成績収集はraces起点で駆動する。1回の収集で処理する未取得レース数の上限。
    RESULTS_RACES_PER_RUN: int = Field(default=50, ge=1)
    # 馬の血統を何代まで収集するか(5代血統表)。
    HORSE_PEDIGREE_GENERATIONS: int = Field(default=5, ge=1, le=5)

    # IPAT (JRA即時購入) 自動操作
    IPAT_SUBSCRIBER_NUMBER: str = ""
    IPAT_PIN: str = ""
    IPAT_PARS_NUMBER: str = ""
    IPAT_DRY_RUN: bool = True

    # WebUI管理ログイン
    ADMIN_LOGIN_ID: str = ""
    ADMIN_PASSWORD: str = ""
    # 管理セッションの有効期間(秒)とログイン試行のレート制限
    ADMIN_SESSION_SECONDS: int = Field(default=60 * 60 * 12, ge=60)
    ADMIN_LOGIN_MAX_ATTEMPTS: int = Field(default=10, ge=1)
    ADMIN_LOGIN_WINDOW_SECONDS: int = Field(default=300, ge=1)
    # HTTPS 配信時に Cookie へ Secure 属性を付ける(リバースプロキシ等でTLS終端する場合に true)
    ADMIN_COOKIE_SECURE: bool = False

    @field_validator("BETTING_MODE")
    @classmethod
    def _check_betting_mode(cls, value: str) -> str:
        if value not in ("prod", "sim"):
            raise ValueError(f"BETTING_MODE は 'prod' か 'sim' を指定してください: {value!r}")
        return value

    @model_validator(mode="after")
    def _check_bet_amount(self) -> "Settings":
        if self.BET_AMOUNT < 100 or self.BET_AMOUNT % 100 != 0:
            raise ValueError(
                f"BET_AMOUNT は100円以上・100円単位で指定してください: {self.BET_AMOUNT!r}"
            )
        return self


settings = Settings()
