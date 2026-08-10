"""End-to-end Phase 5/6 report flow on a real Postgres.

Only the LLM provider is mocked — the real model is never called in CI. The
Celery task runs synchronously (``task_always_eager``) with its sync DB session
and provider swapped for test-bound twins, so ``POST /api/v1/report`` exercises the
full enqueue → WP-context → generate → persist path against the test database.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.tasks.celery_app as celery_module
from app.config import settings
from app.database import get_async_db
from app.models.db import Game, PlayerReport
from app.tasks.celery_app import celery_app

PLAYER = "hero"
OPPONENT = "villain"
THRESHOLD = settings.REPORT_REFRESH_THRESHOLD
MOCK_REPORT_TEXT = "MOCK REPORT: deterministic scouting narrative for testing."


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def eager_task(monkeypatch, sync_session_factory):
    """Make ``generate_player_report.delay`` run inline against the test DB.

    Swaps the task's LLM provider for a deterministic mock and its sync session
    for one bound to the test Postgres, then flips Celery into eager mode so the
    POST endpoint's ``.delay`` executes the task synchronously (no Redis).
    """

    generated_messages: list[tuple[str, str]] = []

    class _FakeProvider:
        def generate(self, system: str, user: str) -> str:
            generated_messages.append((system, user))
            return MOCK_REPORT_TEXT

    monkeypatch.setattr(celery_module, "get_llm_provider", lambda: _FakeProvider())

    @contextmanager
    def _sync_session():
        session = sync_session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(celery_module, "get_sync_db_session", _sync_session)

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield generated_messages
    celery_app.conf.task_always_eager = False
    celery_app.conf.task_eager_propagates = False


@pytest_asyncio.fixture
async def client(app, async_db_url, migrated_db, auth_headers):
    """ASGI client whose ``get_async_db`` points at the test Postgres."""
    engine = create_async_engine(async_db_url, pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def _override():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_async_db] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=auth_headers,
    ) as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


# ── helpers ─────────────────────────────────────────────────────────────────


def _analyzed_game(idx: int, *, player: str = PLAYER) -> Game:
    """A minimal but schema-valid analyzed game with ``player`` as White."""
    return Game(
        unique_id=f"rg{idx}",
        white_player=player,
        black_player=OPPONENT,
        result="1-0",
        winner="White",
        opening_name="Sicilian Defense",
        time_control="blitz",
        date_played=date(2026, 1, 1),
        pgn_content="1. e4 c5 1-0",
        is_analyzed=True,
        analysis_data={
            "summary": {
                "white_acpl": 30,
                "black_acpl": 30,
                "advantage_lost": {"white": False, "black": False},
            },
            "moves": [
                {
                    "ply": 1,
                    "move_num": 1,
                    "color": "White",
                    "san": "e4",
                    "piece": "P",
                    "eval_before": 0,
                    "eval_after": -100,
                    "cp_loss": 20,
                    "classification": "excellent",
                    "phase": "opening",
                },
                {
                    "ply": 3,
                    "move_num": 2,
                    "color": "White",
                    "san": "Nf3",
                    "piece": "N",
                    "eval_before": -100,
                    "eval_after": -50,
                    "cp_loss": 40,
                    "classification": "good",
                    "phase": "opening",
                },
            ],
        },
    )


async def _seed_games(async_session, count: int, *, start: int = 0) -> None:
    """Insert ``count`` analyzed games for ``PLAYER`` and commit them."""
    async_session.add_all([_analyzed_game(i) for i in range(start, start + count)])
    await async_session.commit()


def _report_snapshot(sync_session_factory, *, language: str = "en") -> dict | None:
    """Read the persisted report row's fields (detached-safe) or ``None``."""
    with sync_session_factory() as session:
        row = session.execute(
            select(PlayerReport).where(
                PlayerReport.player_name == PLAYER,
                PlayerReport.language == language,
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return {
            "status": row.status,
            "report_text": row.report_text,
            "analyzed_games_count": row.analyzed_games_count,
            "last_game_played_at": row.last_game_played_at,
        }


# ── tests ───────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_report_flow(async_session, client, eager_task, sync_session_factory):
    await _seed_games(async_session, THRESHOLD)

    resp = await client.post(f"/api/v1/report/{PLAYER}")
    assert resp.status_code == 202
    assert resp.json()["action"] == "generate"

    snapshot = _report_snapshot(sync_session_factory)
    assert snapshot is not None
    assert snapshot["status"] == "ready"
    assert snapshot["report_text"] == MOCK_REPORT_TEXT
    assert snapshot["analyzed_games_count"] == THRESHOLD

    assert len(eager_task) == 1
    _, user_digest = eager_task[0]
    assert "OVERALL WIN-PROBABILITY LOSS" in user_digest
    assert "overall: 4.55" in user_digest
    assert "wp loss" in user_digest
    assert "ACPL" not in user_digest
    assert "centipawn" not in user_digest.lower()

    get_resp = await client.get(f"/api/v1/report/{PLAYER}")
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["report_text"] == MOCK_REPORT_TEXT
    assert body["analyzed_games_count"] == THRESHOLD
    assert body["is_stale"] is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_insufficient_games_no_generation(
    async_session, client, eager_task, sync_session_factory
):
    await _seed_games(async_session, THRESHOLD - 1)

    resp = await client.post(f"/api/v1/report/{PLAYER}")
    assert resp.status_code == 200
    assert resp.json()["action"] == "insufficient_games"

    assert _report_snapshot(sync_session_factory) is None

    get_resp = await client.get(f"/api/v1/report/{PLAYER}")
    assert get_resp.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_regenerates_after_threshold(async_session, client, eager_task, sync_session_factory):
    await _seed_games(async_session, THRESHOLD)
    first = await client.post(f"/api/v1/report/{PLAYER}")
    assert first.status_code == 202
    assert _report_snapshot(sync_session_factory)["analyzed_games_count"] == THRESHOLD

    # Pour in enough new analyzed games to cross the refresh threshold.
    await _seed_games(async_session, THRESHOLD, start=THRESHOLD)

    stale = await client.get(f"/api/v1/report/{PLAYER}")
    assert stale.status_code == 200
    stale_body = stale.json()
    assert stale_body["is_stale"] is True
    assert stale_body["analyzed_games_count"] == THRESHOLD  # still the old snapshot

    regen = await client.post(f"/api/v1/report/{PLAYER}")
    assert regen.status_code == 202
    assert regen.json()["action"] == "generate"

    snapshot = _report_snapshot(sync_session_factory)
    assert snapshot["analyzed_games_count"] == 2 * THRESHOLD
    assert snapshot["status"] == "ready"

    fresh = await client.get(f"/api/v1/report/{PLAYER}")
    assert fresh.json()["analyzed_games_count"] == 2 * THRESHOLD
    assert fresh.json()["is_stale"] is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_deleted_report_can_regenerate(
    async_session, client, eager_task, sync_session_factory
):
    await _seed_games(async_session, THRESHOLD)
    await client.post(f"/api/v1/report/{PLAYER}")
    assert _report_snapshot(sync_session_factory) is not None

    # Deleting the row is the supported way to force a regeneration (no `force`).
    with sync_session_factory() as session:
        session.execute(PlayerReport.__table__.delete().where(PlayerReport.player_name == PLAYER))
        session.commit()
    assert _report_snapshot(sync_session_factory) is None

    resp = await client.post(f"/api/v1/report/{PLAYER}")
    assert resp.status_code == 202
    assert resp.json()["action"] == "generate"

    snapshot = _report_snapshot(sync_session_factory)
    assert snapshot is not None
    assert snapshot["status"] == "ready"
    assert snapshot["report_text"] == MOCK_REPORT_TEXT
    assert snapshot["analyzed_games_count"] == THRESHOLD


@pytest.mark.integration
@pytest.mark.asyncio
async def test_below_threshold_returns_cached(
    async_session, client, eager_task, sync_session_factory
):
    await _seed_games(async_session, THRESHOLD)
    await client.post(f"/api/v1/report/{PLAYER}")
    assert _report_snapshot(sync_session_factory)["analyzed_games_count"] == THRESHOLD

    # A handful of new games — below the threshold, so no regeneration.
    await _seed_games(async_session, THRESHOLD - 1, start=THRESHOLD)

    resp = await client.post(f"/api/v1/report/{PLAYER}")
    assert resp.status_code == 200
    assert resp.json()["action"] == "up_to_date"

    get_resp = await client.get(f"/api/v1/report/{PLAYER}")
    body = get_resp.json()
    assert body["report_text"] == MOCK_REPORT_TEXT
    assert body["analyzed_games_count"] == THRESHOLD  # unchanged snapshot
    assert body["is_stale"] is False
