"""
SQLAlchemy engine + session management.

Uses connection pooling (pool_size / max_overflow) as required by the
architecture doc, and a pool_pre_ping so stale Supabase connections
are recycled automatically instead of raising errors.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=1800,  # recycle connections every 30 min (Supabase pooler friendly)
    future=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)

Base = declarative_base()


def get_db():
    """FastAPI dependency — yields a DB session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
