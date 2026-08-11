import os
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from alembic import command


def _alembic_config() -> Config:
    repo_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "alembic"))
    return cfg


@pytest.fixture(scope="session")
def test_db_env():
    """
    Integration tests need a real Postgres.

    By default we reuse docker-compose settings and switch DB_NAME to chess_lab_test.
    """
    # Force test DB settings even if a local `.env` already set DB_*.
    os.environ["DB_HOST"] = "localhost"
    # Use the dedicated test Postgres (docker-compose service `db_test`)
    os.environ["DB_PORT"] = "5433"
    os.environ["DB_USER"] = "chess"
    os.environ["DB_PASSWORD"] = "chess"
    os.environ["DB_NAME"] = "chess_lab_test"
    yield


@pytest.fixture(scope="session")
def async_db_url(test_db_env) -> str:
    user = os.environ["DB_USER"]
    password = os.environ["DB_PASSWORD"]
    host = os.environ["DB_HOST"]
    port = os.environ["DB_PORT"]
    name = os.environ["DB_NAME"]
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{name}"


@pytest_asyncio.fixture
async def migrated_db(async_db_url, test_db_env):
    """
    Ensure test DB exists and Alembic migrations applied.
    """
    # Create DB if missing (connect to postgres maintenance DB)
    maintenance_url = async_db_url.rsplit("/", 1)[0] + "/postgres"
    db_name = async_db_url.rsplit("/", 1)[1]

    admin_engine = create_async_engine(maintenance_url, isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        exists = await conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": db_name},
        )
        if not exists:
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    await admin_engine.dispose()

    # Run migrations using a URL explicitly set in Alembic config.
    user = os.environ["DB_USER"]
    password = os.environ["DB_PASSWORD"]
    host = os.environ["DB_HOST"]
    port = os.environ["DB_PORT"]
    name = os.environ["DB_NAME"]
    sync_url = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"

    cfg = _alembic_config()
    cfg.set_main_option("sqlalchemy.url", sync_url)
    command.upgrade(cfg, "head")

    yield


@pytest.fixture
def sync_session_factory(test_db_env):
    """A sessionmaker bound to the test Postgres — Celery tasks use sync sessions.

    Migrations are not requested here (that fixture is async); tests pair this
    with ``async_session`` / ``migrated_db`` when they need the schema.
    """
    user = os.environ["DB_USER"]
    password = os.environ["DB_PASSWORD"]
    host = os.environ["DB_HOST"]
    port = os.environ["DB_PORT"]
    name = os.environ["DB_NAME"]
    sync_url = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"

    engine = create_engine(sync_url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    yield factory
    engine.dispose()


@pytest_asyncio.fixture
async def async_session(async_db_url, migrated_db):
    engine = create_async_engine(async_db_url, pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        # Guard rail: never allow destructive cleanup on a non-test database.
        db_name = os.environ.get("DB_NAME", "")
        db_port = os.environ.get("DB_PORT", "")
        if not db_name.endswith("_test") or db_port != "5433":
            raise RuntimeError(
                f"Refusing to run integration test DB cleanup on DB_NAME={db_name!r}, DB_PORT={db_port!r}. "
                "Expected a dedicated test DB (name endswith '_test' and port 5433)."
            )

        # Ensure clean state between tests; otherwise UNIQUE constraints may fail
        # across reruns or multiple tests.
        await session.execute(text("TRUNCATE TABLE games, player_reports RESTART IDENTITY CASCADE"))
        await session.commit()
        yield session
        await session.rollback()

    await engine.dispose()
