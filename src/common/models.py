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
    - FAILED: prodモードで購入操作が失敗した状態(実際のお金は動いていない)。
    """

    PENDING = "pending"
    PLACED = "placed"
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
    horse_name = Column(String, nullable=False)
    jockey = Column(String)
    weight = Column(Float)
    odds = Column(Float)
    finish_position = Column(Integer)

    race = relationship("Race", back_populates="entries")


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
    job_name = Column(String(20), nullable=False)  # collect / backfill / predict / settle / train
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
    amount = Column(Float, nullable=False)
    odds_at_bet = Column(Float)
    payout = Column(Float)
    is_settled = Column(Boolean, default=False)
    placed_at = Column(DateTime, default=now_jst)

    race = relationship("Race", back_populates="bets")
    entry = relationship("Entry")
