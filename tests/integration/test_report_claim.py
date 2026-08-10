"""The report claim against a real Postgres.

``ON CONFLICT ... WHERE`` and the lease comparison are database behaviour, so
they are only meaningfully covered here: a mocked session would just replay
whatever the test assumed.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.models.db import PlayerReport
from app.services.report_repository import (
    get_report,
    is_generation_stale,
    mark_failed_sync,
    release_generating,
    save_report_result_sync,
    upsert_generating,
)

PLAYER = "MagnusCarlsen"
PLAYER_ALTERNATE = "magnuscarlsen"
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
    await session.execute(text("UPDATE player_reports SET updated_at = now() - interval '1 day'"))
    await session.commit()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_second_claim_loses_while_generation_is_live(async_session):
    assert await upsert_generating(async_session, PLAYER, LANGUAGE, 20) is True
    assert await upsert_generating(async_session, PLAYER_ALTERNATE, LANGUAGE, 20) is False

    row = await _row(async_session)
    row_count = await async_session.scalar(select(func.count()).select_from(PlayerReport))
    assert row_count == 1
    assert row.player_name == PLAYER
    assert row.status == "generating"
    assert row.report_text is None
    assert row.analyzed_games_count == 20


@pytest.mark.integration
@pytest.mark.asyncio
async def test_expired_lease_is_reclaimable(async_session):
    assert await upsert_generating(async_session, PLAYER, LANGUAGE, 20) is True
    await _expire_lease(async_session)

    assert await is_generation_stale(async_session, await _row(async_session)) is True
    assert await upsert_generating(async_session, PLAYER_ALTERNATE, LANGUAGE, 25) is True

    row = await _row(async_session)
    assert row.player_name == PLAYER
    assert row.analyzed_games_count == 20


@pytest.mark.integration
@pytest.mark.asyncio
async def test_claim_keeps_the_previous_report_and_snapshot(async_session):
    """A failed generation must not leave the old text looking up to date."""
    await _seed_ready_report(async_session, count=10)

    assert await upsert_generating(async_session, PLAYER_ALTERNATE, LANGUAGE, 40) is True

    row = await _row(async_session)
    assert row.status == "generating"
    assert row.report_text == "previous text"
    assert row.analyzed_games_count == 10


@pytest.mark.integration
@pytest.mark.asyncio
async def test_release_deletes_a_placeholder_row(async_session):
    await upsert_generating(async_session, PLAYER, LANGUAGE, 20)

    await release_generating(async_session, PLAYER_ALTERNATE, LANGUAGE)

    assert await _row(async_session) is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_release_keeps_a_previous_report_servable(async_session):
    await _seed_ready_report(async_session, count=10)
    await upsert_generating(async_session, PLAYER, LANGUAGE, 40)

    await release_generating(async_session, PLAYER_ALTERNATE, LANGUAGE)

    row = await _row(async_session)
    assert row.status == "failed"
    assert row.report_text == "previous text"
    assert row.analyzed_games_count == 10


@pytest.mark.integration
@pytest.mark.asyncio
async def test_report_lookup_is_case_insensitive(async_session):
    await _seed_ready_report(async_session)

    report = await get_report(async_session, PLAYER_ALTERNATE, LANGUAGE)

    assert report is not None
    assert report.player_name == PLAYER
    assert report.report_text == "previous text"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cross_case_save_and_fail_update_the_existing_row(
    async_session,
    sync_session_factory,
):
    assert await upsert_generating(async_session, PLAYER, LANGUAGE, 20) is True

    with sync_session_factory() as session:
        save_report_result_sync(
            session,
            PLAYER_ALTERNATE,
            LANGUAGE,
            report_text="new text",
            analyzed_games_count=20,
            last_game_played_at=datetime(2026, 2, 1),
        )
        session.commit()

    with sync_session_factory() as session:
        mark_failed_sync(session, PLAYER.upper(), LANGUAGE)
        session.commit()

    row = await _row(async_session)
    row_count = await async_session.scalar(select(func.count()).select_from(PlayerReport))
    assert row_count == 1
    assert row.player_name == PLAYER
    assert row.report_text == "new text"
    assert row.analyzed_games_count == 20
    assert row.status == "failed"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_functional_index_rejects_cross_case_duplicate(async_session):
    async_session.add(
        PlayerReport(
            player_name=PLAYER,
            language=LANGUAGE,
            report_text="first",
            status="ready",
        )
    )
    await async_session.commit()

    async_session.add(
        PlayerReport(
            player_name=PLAYER_ALTERNATE,
            language=LANGUAGE,
            report_text="duplicate",
            status="ready",
        )
    )
    with pytest.raises(IntegrityError):
        await async_session.commit()
    await async_session.rollback()

    row_count = await async_session.scalar(select(func.count()).select_from(PlayerReport))
    assert row_count == 1
