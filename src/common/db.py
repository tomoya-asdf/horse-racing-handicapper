from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from src.common.config import settings
from src.common.models import Base

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    # 収集/予測/APIが同時にDBを使うため、枯渇しにくいよう明示的にプールを確保する。
    # 上限到達時は最大30秒待ってから諦める(無期限ブロックを避ける)。
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=30,
    pool_recycle=1800,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# 既存テーブルへ後から追加した列のための簡易マイグレーション。
# create_all は新規テーブルしか作らないため、稼働中DBには列が増えない。
# Postgresの ADD COLUMN IF NOT EXISTS で冪等に追加する。
_MIGRATIONS = (
    "ALTER TABLE entries ADD COLUMN IF NOT EXISTS jockey_id VARCHAR",
    "ALTER TABLE entries ADD COLUMN IF NOT EXISTS popularity INTEGER",
    "ALTER TABLE entries ADD COLUMN IF NOT EXISTS horse_id VARCHAR",
    "CREATE INDEX IF NOT EXISTS ix_entries_horse_id ON entries (horse_id)",
    "ALTER TABLE entries ADD COLUMN IF NOT EXISTS sex VARCHAR",
    "ALTER TABLE entries ADD COLUMN IF NOT EXISTS age INTEGER",
    "ALTER TABLE entries ADD COLUMN IF NOT EXISTS trainer VARCHAR",
    "ALTER TABLE entries ADD COLUMN IF NOT EXISTS trainer_id VARCHAR",
    "ALTER TABLE entries ADD COLUMN IF NOT EXISTS horse_weight INTEGER",
    "ALTER TABLE entries ADD COLUMN IF NOT EXISTS horse_weight_diff INTEGER",
    "ALTER TABLE entries ADD COLUMN IF NOT EXISTS pre_race_odds DOUBLE PRECISION",
    "ALTER TABLE entries ADD COLUMN IF NOT EXISTS final_odds DOUBLE PRECISION",
    "ALTER TABLE races ADD COLUMN IF NOT EXISTS distance INTEGER",
    "ALTER TABLE races ADD COLUMN IF NOT EXISTS track_type VARCHAR",
    "ALTER TABLE races ADD COLUMN IF NOT EXISTS direction VARCHAR",
    "ALTER TABLE races ADD COLUMN IF NOT EXISTS going VARCHAR",
    "ALTER TABLE races ADD COLUMN IF NOT EXISTS weather VARCHAR",
    "ALTER TABLE races ADD COLUMN IF NOT EXISTS race_class VARCHAR",
    "ALTER TABLE horses ADD COLUMN IF NOT EXISTS sire_id VARCHAR",
    "ALTER TABLE horses ADD COLUMN IF NOT EXISTS sire_name VARCHAR",
    "ALTER TABLE bets ADD COLUMN IF NOT EXISTS combination VARCHAR",
    "ALTER TABLE bets ADD COLUMN IF NOT EXISTS model_version VARCHAR",
    "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS raw_score DOUBLE PRECISION",
)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        for statement in _MIGRATIONS:
            conn.execute(text(statement))


def get_session() -> Session:
    return SessionLocal()


@contextmanager
def session_scope() -> Iterator[Session]:
    """セッションのライフサイクル(close、エラー時rollback)を一元管理する。

    ``with session_scope() as session:`` で使う。読み取り専用でも安全に使える。
    呼び出し側で ``commit()`` を明示する方針は従来どおりとし(書き込みの境界を
    呼び出し側に残す)、ここでは確実な close と、例外送出時の rollback を保証する。
    """
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI 依存注入用。``session: Session = Depends(get_db)`` で受け取る。"""
    with session_scope() as session:
        yield session
