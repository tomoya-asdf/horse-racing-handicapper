from enum import Enum

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

from src.common.timeutils import now_jst

Base = declarative_base()


class BettingMode(str, Enum):
    PROD = "prod"
    SIM = "sim"


class BetStatus(str, Enum):
    """賭けの購入状態。

    - PENDING: prodモードで購入操作の開始前に記録された状態。購入の途中で
      プロセスが落ちた場合はこの状態のまま残る(購入されたかは要手動確認)。
    - PLACED: simモードでの記録、またはprodモードで購入操作が成功した状態。
    - DRY_RUN: prodモードだがIPAT_DRY_RUN=trueのため実購入しなかった状態。
    - FAILED: prodモードで購入操作が失敗した状態(実際のお金は動いていない)。
    """

    PENDING = "pending"
    PLACED = "placed"
    DRY_RUN = "dry_run"
    FAILED = "failed"


class Race(Base):
    __tablename__ = "races"

    id = Column(Integer, primary_key=True)
    race_key = Column(String, unique=True, nullable=False)
    race_date = Column(Date, nullable=False)
    venue = Column(String, nullable=False)
    race_number = Column(Integer, nullable=False)
    race_name = Column(String)
    start_time = Column(DateTime)
    # レース条件(出馬表ヘッダから取得)。距離・コースは事前に判明するが、
    # 馬場状態・天候は当日にならないと出ないため収集の度に更新する。
    distance = Column(Integer)  # 距離(m)
    track_type = Column(String)  # 芝 / ダート / 障害
    direction = Column(String)  # 右 / 左 / 直
    going = Column(String)  # 馬場状態(良/稍重/重/不良)
    weather = Column(String)  # 天候(晴/曇/雨 等)
    race_class = Column(String)  # クラス・格(G1/G2/G3/オープン/3勝クラス/未勝利/新馬 等)
    created_at = Column(DateTime, default=now_jst)

    entries = relationship("Entry", back_populates="race", cascade="all, delete-orphan")
    predictions = relationship("Prediction", back_populates="race", cascade="all, delete-orphan")
    bets = relationship("Bet", back_populates="race", cascade="all, delete-orphan")


class Entry(Base):
    __tablename__ = "entries"
    __table_args__ = (UniqueConstraint("race_id", "horse_number", name="uq_entries_race_horse"),)

    id = Column(Integer, primary_key=True)
    race_id = Column(Integer, ForeignKey("races.id"), nullable=False)
    horse_number = Column(Integer, nullable=False)
    horse_id = Column(String, index=True)  # netkeibaの馬ID(過去成績 horse_results との紐付けに使う)
    horse_name = Column(String, nullable=False)
    jockey = Column(String)
    jockey_id = Column(String)  # netkeibaの騎手ID(騎手名は同姓同名がありうるため学習にはIDを使う)
    weight = Column(Float)
    odds = Column(Float)  # 発走前は予想オッズ、発走後は最終オッズ(収集の度に上書き)
    popularity = Column(Integer)  # 人気順位(発走前は予想人気)。netkeibaから取得、無ければオッズ昇順で導出
    finish_position = Column(Integer)

    race = relationship("Race", back_populates="entries")


class Horse(Base):
    """馬マスタ。過去成績(horse_results)の取得済み管理に使う。

    ``results_fetched_at`` を見て、未取得・古い馬だけを差分的に再取得する
    (新馬など過去走が0件の馬を毎回取りに行かないようにするため、取得を試みたら
    結果が0件でもこの行を作る)。
    """

    __tablename__ = "horses"

    horse_id = Column(String, primary_key=True)
    name = Column(String)
    sire_id = Column(String)  # 父のnetkeiba馬ID(血統特徴量に使う。距離・芝ダ適性の遺伝)
    sire_name = Column(String)  # 父名(表示用)
    results_fetched_at = Column(DateTime)


class HorseResult(Base):
    """馬ごとの過去レース成績(netkeibaの馬ページの成績表1行=1レコード)。

    特徴量作成(直近n走の集計など)の元データ。horse_id + race_key で一意。
    """

    __tablename__ = "horse_results"
    __table_args__ = (
        UniqueConstraint("horse_id", "race_key", name="uq_horse_results_horse_race"),
    )

    id = Column(Integer, primary_key=True)
    horse_id = Column(String, index=True, nullable=False)
    race_key = Column(String)  # 過去レースのnetkeiba race_id(取得できた場合)
    race_date = Column(Date)
    venue = Column(String)  # 開催(例: "2中山5")。場名そのものではなく開催表記
    race_name = Column(String)
    field_size = Column(Integer)  # 頭数
    post_position = Column(Integer)  # 枠番
    horse_number = Column(Integer)  # 馬番
    odds = Column(Float)  # 単勝オッズ
    popularity = Column(Integer)  # 人気
    finish_position = Column(Integer)  # 着順(中止・除外等は取得できずNone)
    jockey = Column(String)
    jockey_id = Column(String)
    weight = Column(Float)  # 斤量
    distance = Column(Integer)  # 距離(m)
    track_type = Column(String)  # 芝 / ダート / 障害
    going = Column(String)  # 馬場状態(良/稍重/重/不良)
    time_seconds = Column(Float)  # 走破タイムを秒に換算
    margin = Column(String)  # 着差(クビ・1.1/2 等の表記をそのまま保持)
    passing = Column(String)  # 通過順(例: "3-3-2-1")
    last_3f = Column(Float)  # 上がり3F
    horse_weight = Column(Integer)  # 馬体重
    created_at = Column(DateTime, default=now_jst)


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (
        UniqueConstraint("entry_id", "model_version", name="uq_predictions_entry_model"),
    )

    id = Column(Integer, primary_key=True)
    race_id = Column(Integer, ForeignKey("races.id"), nullable=False)
    entry_id = Column(Integer, ForeignKey("entries.id"), nullable=False)
    model_version = Column(String, nullable=False)
    score = Column(Float, nullable=False)
    created_at = Column(DateTime, default=now_jst)

    race = relationship("Race", back_populates="predictions")


class JobTrigger(str, Enum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"


class JobStatus(str, Enum):
    QUEUED = "queued"  # WebUIから実行依頼済み。担当サービスのポーリング待ち
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class JobRun(Base):
    """ジョブの実行キュー兼実行履歴。

    WebUI(API)が status=queued の行を作成し、担当サービス(collector/predictor)が
    ポーリングして実行する。スケジュール実行も同じテーブルに記録する。
    """

    __tablename__ = "job_runs"

    id = Column(Integer, primary_key=True)
    job_name = Column(String(20), nullable=False)  # collect / backfill / predict / bet_decide / settle / train
    trigger = Column(String(10), nullable=False)
    status = Column(String(10), nullable=False)
    params = Column(String)  # ジョブへの引数(JSON)。backfillの日付範囲など
    detail = Column(String)  # 実行結果の要約。失敗時はエラー内容
    created_at = Column(DateTime, default=now_jst)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)


class AppSetting(Base):
    """WebUIから変更できる設定のキー/値ストア。.envの値を上書きする。"""

    __tablename__ = "app_settings"

    key = Column(String(50), primary_key=True)
    value = Column(String, nullable=False)
    updated_at = Column(DateTime, default=now_jst, onupdate=now_jst)


class Bet(Base):
    __tablename__ = "bets"

    id = Column(Integer, primary_key=True)
    race_id = Column(Integer, ForeignKey("races.id"), nullable=False)
    entry_id = Column(Integer, ForeignKey("entries.id"), nullable=False)
    mode = Column(String(10), nullable=False)
    status = Column(String(10), nullable=False, default=BetStatus.PLACED.value)
    bet_type = Column(String, nullable=False)
    # 馬連など複数頭の券種の買い目(例 "4-9"、馬番昇順)。単勝はNullでentryが対象馬
    combination = Column(String)
    amount = Column(Float, nullable=False)
    odds_at_bet = Column(Float)
    payout = Column(Float)
    is_settled = Column(Boolean, default=False)
    placed_at = Column(DateTime, default=now_jst)

    race = relationship("Race", back_populates="bets")
    entry = relationship("Entry")
