from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from src.common.config import settings
from src.common.models import Base

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
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
    "CREATE INDEX IF NOT EXISTS ix_jockey_results_jockey_id ON jockey_results (jockey_id)",
    "CREATE INDEX IF NOT EXISTS ix_trainer_results_trainer_id ON trainer_results (trainer_id)",
)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        for statement in _MIGRATIONS:
            conn.execute(text(statement))


def get_session() -> Session:
    return SessionLocal()
