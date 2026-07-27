"""The report claim against a real Postgres.

``ON CONFLICT ... WHERE`` and the lease comparison are database behaviour, so
they are only meaningfully covered here: a mocked session would just replay
whatever the test assumed.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select, text

from app.models.db import PlayerReport
from app.services.report_repository import (
    is_generation_stale,
    release_generating,
    upsert_generating,
)

PLAYER = "hero"
LANGUAGE = "en"


async def _row(session) -> PlayerReport | None:
    session.expire_all()
    return await session.scalar(
        select(PlayerReport).where(
            PlayerReport.player_name == PLAYER,
            PlayerReport.language == LANGUAGE,
        )
    )


async def _seed_ready_report(session, *, count: int = 10) -> None:
    session.add(
        PlayerReport(
            player_name=PLAYER,
            language=LANGUAGE,
            report_text="previous text",
            analyzed_games_count=count,
            status="ready",
            last_game_played_at=datetime(2026, 1, 1),
        )
    )
    await session.commit()


async def _expire_lease(session) -> None:
    """Backdate the row far enough that its generation lease has run out."""
    await session.execute(
        text("UPDATE player_reports SET updated_at = now() - interval '1 day'")
    )
    await session.commit()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_second_claim_loses_while_generation_is_live(async_session):
    assert await upsert_generating(async_session, PLAYER, LANGUAGE, 20) is True
    assert await upsert_generating(async_session, PLAYER, LANGUAGE, 20) is False

    row = await _row(async_session)
    assert row.status == "generating"
    assert row.report_text is None
    assert row.analyzed_games_count == 20


@pytest.mark.integration
@pytest.mark.asyncio
async def test_expired_lease_is_reclaimable(async_session):
    assert await upsert_generating(async_session, PLAYER, LANGUAGE, 20) is True
    await _expire_lease(async_session)

    assert await is_generation_stale(async_session, await _row(async_session)) is True
    assert await upsert_generating(async_session, PLAYER, LANGUAGE, 25) is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_claim_keeps_the_previous_report_and_snapshot(async_session):
    """A failed generation must not leave the old text looking up to date."""
    await _seed_ready_report(async_session, count=10)

    assert await upsert_generating(async_session, PLAYER, LANGUAGE, 40) is True

    row = await _row(async_session)
    assert row.status == "generating"
    assert row.report_text == "previous text"
    assert row.analyzed_games_count == 10


@pytest.mark.integration
@pytest.mark.asyncio
async def test_release_deletes_a_placeholder_row(async_session):
    await upsert_generating(async_session, PLAYER, LANGUAGE, 20)

    await release_generating(async_session, PLAYER, LANGUAGE)

    assert await _row(async_session) is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_release_keeps_a_previous_report_servable(async_session):
    await _seed_ready_report(async_session, count=10)
    await upsert_generating(async_session, PLAYER, LANGUAGE, 40)

    await release_generating(async_session, PLAYER, LANGUAGE)

    row = await _row(async_session)
    assert row.status == "failed"
    assert row.report_text == "previous text"
    assert row.analyzed_games_count == 10
