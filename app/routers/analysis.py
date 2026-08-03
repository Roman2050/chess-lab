import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_async_db
from app.schemas.analysis import AnalysisProgress, BatchAnalysisResponse
from app.security import require_mvp_api_key
from app.services.analysis_queue import (
    get_analysis_progress,
    get_unanalyzed_game_ids,
)
from app.tasks.celery_app import analyze_game

router = APIRouter(prefix="/analyze", tags=["Analysis"])


@router.post(
    "/player/{username}",
    response_model=BatchAnalysisResponse,
    dependencies=[Depends(require_mvp_api_key)],
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
    game_ids = await get_unanalyzed_game_ids(db, username)

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


@router.get("/player/{username}/status", response_model=AnalysisProgress)
async def get_player_analysis_status(
    username: str,
    db: AsyncSession = Depends(get_async_db),
):
    """Read analysis progress (total / analyzed / pending) straight from the DB."""
    progress = await get_analysis_progress(db, username)

    if progress["total"] == 0:
        raise HTTPException(status_code=404, detail="Player not found")

    return AnalysisProgress(player=username, **progress)
