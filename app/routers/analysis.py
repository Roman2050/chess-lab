import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_async_db
from app.schemas.analysis import AnalysisProgress, BatchAnalysisResponse
from app.security import require_analysis_quota
from app.services.analysis_queue import (
    get_analysis_progress,
    get_unanalyzed_game_ids,
)
from app.tasks.celery_app import analyze_game

router = APIRouter(prefix="/analyze")

OPERATOR_ERROR_RESPONSES = {
    401: {"description": "Missing or invalid operator API key."},
    429: {"description": "Analysis quota exhausted. Retry after the seconds in `Retry-After`."},
    503: {"description": "The quota backend is unavailable."},
}


@router.post(
    "/player/{username}",
    response_model=BatchAnalysisResponse,
    dependencies=[Depends(require_analysis_quota)],
    tags=["Analysis"],
    summary="Queue a player's pending analyses",
    description=(
        "**Operator-only.** Queue at most `MAX_ANALYSIS_TASKS_PER_REQUEST` claimable "
        "games in stable ID order. Each game becomes a separate Celery task. Poll "
        "the status endpoint; PostgreSQL, not a Celery result backend, is authoritative."
    ),
    response_description="Player identity and number of game tasks queued.",
    responses={
        **OPERATOR_ERROR_RESPONSES,
        404: {"description": "The player has no claimable games."},
    },
)
async def enqueue_player_analysis(
    username: str,
    db: AsyncSession = Depends(get_async_db),
):
    """Fan out one Celery task per not-yet-analyzed game of the player.

    One small task per game (not a mega-batch task) so a worker crash loses a
    single game, and `-c N` on the worker gives parallelism. Enqueuing the same
    game twice is harmless: the task claims it atomically (see §7).
    """
    task_limit = settings.MAX_ANALYSIS_TASKS_PER_REQUEST
    game_ids = await get_unanalyzed_game_ids(db, username, limit=task_limit)
    game_ids = game_ids[:task_limit]

    if not game_ids:
        raise HTTPException(status_code=404, detail="No unanalyzed games for player")

    # `.delay()` publishes to Redis synchronously (kombu/redis-py blocks); for a
    # large fan-out that would stall the event loop, so run the enqueue loop off
    # the loop in a worker thread.
    def _enqueue_all() -> None:
        for game_id in game_ids:
            analyze_game.delay(game_id)

    await asyncio.to_thread(_enqueue_all)

    return BatchAnalysisResponse(
        status="queued",
        player=username,
        queued_count=len(game_ids),
    )


@router.get(
    "/player/{username}/status",
    response_model=AnalysisProgress,
    tags=["Analysis"],
    summary="Read a player's analysis progress",
    description=(
        "**Public read.** Return database-backed total, analyzed, and pending game "
        "counts. Use this endpoint to poll after an operator queues analysis."
    ),
    response_description="Current analysis progress for the player.",
    responses={404: {"description": "Player not found."}},
)
async def get_player_analysis_status(
    username: str,
    db: AsyncSession = Depends(get_async_db),
):
    """Read analysis progress (total / analyzed / pending) straight from the DB."""
    progress = await get_analysis_progress(db, username)

    if progress["total"] == 0:
        raise HTTPException(status_code=404, detail="Player not found")

    return AnalysisProgress(player=username, **progress)
