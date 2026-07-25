"""Concurrency contract of the analysis claim against a real Postgres.

The interesting case is not "claim twice in a row" (that only proves the status
predicate) but two claims overlapping in time. Here the winner deliberately holds
its transaction open while a second connection tries the same claim from another
thread, so the row lock and the post-commit re-evaluation are actually exercised.
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import select

from app.models.db import Game
from app.tasks.celery_app import _claim_stmt

PGN = "1. e4 e5 2. Nf3 Nc6"


async def _seed_game(async_session, unique_id: str, **overrides) -> int:
    game = Game(
        unique_id=unique_id,
        white_player="hero",
        black_player="villain",
        result="1-0",
        winner="White",
        pgn_content=PGN,
        **overrides,
    )
    async_session.add(game)
    await async_session.commit()
    return game.id


async def _status_row(async_session, game_id: int):
    stmt = select(
        Game.analysis_status,
        Game.analysis_attempts,
        Game.analysis_started_at,
        Game.is_analyzed,
    ).where(Game.id == game_id)
    return (await async_session.execute(stmt)).one()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_two_concurrent_claims_one_winner(async_session, sync_session_factory):
    """Overlapping claims: one gets the PGN, the other gets nothing."""
    game_id = await _seed_game(async_session, "claim-race")

    loser_result: dict[str, str | None] = {}

    def _claim_from_other_connection() -> None:
        session = sync_session_factory()
        try:
            loser_result["pgn"] = session.execute(
                _claim_stmt(game_id)
            ).scalar_one_or_none()
            session.commit()
        finally:
            session.close()

    winner = sync_session_factory()
    try:
        assert winner.execute(_claim_stmt(game_id)).scalar_one_or_none() == PGN

        # Winner still holds the row lock — the second claim must wait, not fail
        # and not succeed.
        loser = threading.Thread(target=_claim_from_other_connection)
        loser.start()
        loser.join(timeout=1.0)
        assert loser.is_alive(), "second claim should block on the locked row"

        winner.commit()
        loser.join(timeout=30.0)
        assert not loser.is_alive(), "second claim never unblocked after commit"
    finally:
        winner.rollback()
        winner.close()

    assert loser_result["pgn"] is None

    row = await _status_row(async_session, game_id)
    assert row.analysis_status == "running"
    assert row.analysis_attempts == 1  # only the winner incremented
    assert row.analysis_started_at is not None
    assert row.is_analyzed is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_completed_game_is_not_claimable(async_session, sync_session_factory):
    """A finished game yields no PGN and its attempt counter stays untouched."""
    game_id = await _seed_game(
        async_session,
        "claim-completed",
        is_analyzed=True,
        analysis_status="completed",
    )

    session = sync_session_factory()
    try:
        assert session.execute(_claim_stmt(game_id)).scalar_one_or_none() is None
        session.commit()
    finally:
        session.close()

    row = await _status_row(async_session, game_id)
    assert row.analysis_status == "completed"
    assert row.analysis_attempts == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_failed_game_is_claimable_again(async_session, sync_session_factory):
    """A previous failure is retryable: the claim clears the error and counts up."""
    game_id = await _seed_game(
        async_session,
        "claim-failed",
        analysis_status="failed",
        analysis_error="boom",
        analysis_attempts=1,
    )

    session = sync_session_factory()
    try:
        assert session.execute(_claim_stmt(game_id)).scalar_one_or_none() == PGN
        session.commit()
    finally:
        session.close()

    row = await _status_row(async_session, game_id)
    assert row.analysis_status == "running"
    assert row.analysis_attempts == 2

    error = await async_session.scalar(
        select(Game.analysis_error).where(Game.id == game_id)
    )
    assert error is None
