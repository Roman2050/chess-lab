from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

DATABASE_URL_ASYNC = settings.database_url_async
DATABASE_URL_SYNC = settings.database_url_sync


async_engine = create_async_engine(
    DATABASE_URL_ASYNC, pool_pre_ping=True, echo=False, pool_size=5, max_overflow=10
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_async_db():
    """Used in FastAPI: @router.get(..., db: AsyncSession = Depends(get_async_db))"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


sync_engine = create_engine(
    DATABASE_URL_SYNC, pool_pre_ping=True, echo=False, pool_size=5, max_overflow=10
)

SyncSessionLocal = sessionmaker(
    bind=sync_engine,
    autocommit=False,
    autoflush=False,
)


@contextmanager
def get_sync_db_session():
    """A context manager for use within Celery tasks."""
    session = SyncSessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


Base = declarative_base()
