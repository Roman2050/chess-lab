import asyncio
import httpx
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional

from app.database import get_async_db
from app.models.enums import StandardPerfType
from app.models.db import Game
from app.schemas.games import UploadResponse, PaginatedGames, SortOrder, GameDetail
from app.schemas.stats import (
    PlayerStats,
    OpeningStat,
    MoveAccuracyStat,
)
from app.services.lichess import fetch_games_from_lichess
from app.utils.parser import parse_pgn_text
from app.services.db_manager import bulk_save_games
from app.services.game_queries import get_filtered_games
from app.services.aggregation.acpl import get_player_acpl
from app.services.aggregation.accuracy import (
    get_accuracy_by_phase,
    get_accuracy_by_move_number,
)
from app.services.aggregation.openings import get_opening_stats
from app.services.aggregation.errors import get_error_patterns
from app.tasks.celery_app import analyze_game


router = APIRouter(prefix="/games", tags=["Games Integration"])

@router.post("/lichess/{username}", response_model=UploadResponse)
async def load_from_lichess(
    username: str, 
    max_games: int = Query(50, ge=1, le=50, description="Number of games to upload (max 50)"), 
    perf_type: Optional[StandardPerfType] = None,
    db: AsyncSession = Depends(get_async_db)
):
    """
    Fetches games from the Lichess API for the specified user.
    """
    try:
        raw_pgn = await fetch_games_from_lichess(username, max_games, perf_type)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Lichess API error: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail="Unable to connect to Lichess API")

    parsed_games = await asyncio.to_thread(parse_pgn_text, raw_pgn)

    if not parsed_games:
        return UploadResponse(
            message=f"No standard games found for the user {username}.",
            stats={"saved_new": 0, "total_processed": 0}
        )

    stats = await bulk_save_games(db, parsed_games)
    
    return UploadResponse(
        message=f"Games from Lichess have been successfully processed for {username}",
        stats=stats
    )


@router.post("/upload", response_model=UploadResponse)
async def upload_pgn_file(
    file: UploadFile = File(...), 
    db: AsyncSession = Depends(get_async_db)
):
    """
    Loads games from a standard .pgn file.
    """
    # File format validation
    if not file.filename.lower().endswith(".pgn"):
        raise HTTPException(status_code=400, detail="Only files with the .pgn extension may be uploaded")

    try:
        # Read the entire file into memory
        content = await file.read()
        raw_pgn = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File encoding error. UTF-8 is expected.")

    parsed_games = await asyncio.to_thread(parse_pgn_text, raw_pgn)

    if not parsed_games:
        return UploadResponse(
            message="No valid standard games were found in the file.",
            stats={"saved_new": 0, "total_processed": 0}
        )

    stats = await bulk_save_games(db, parsed_games)
    
    return UploadResponse(
        message=f"File {file.filename} successfully processed",
        stats=stats
    )

@router.get("", response_model=PaginatedGames)
async def get_games_list(
    limit: int = Query(50, ge=1, le=100, description="Number of games on the page"),
    offset: int = Query(0, ge=0, description="Skip elements (for pages)"),
    sort_order: SortOrder = Query(SortOrder.desc, description="Sort (newest or oldest)"),
    player_name: str | None = Query(None, description="Filter by player nickname (played with white or black)"),
    winner: str | None = Query(None, description="Filter by winner (White, Black, Draw)"),
    db: AsyncSession = Depends(get_async_db)
):
    """
    Get a list of games with pagination and filters (without heavy PGN text).
    """
    total, games = await get_filtered_games(db, limit, offset, sort_order, player_name, winner)
    
    return PaginatedGames(
        total_count=total,
        limit=limit,
        offset=offset,
        items=games # SQLAlchemy models will be automatically converted to GameSummary
    )

@router.get("/{game_id}", response_model=GameDetail)
async def get_game_by_id(
    game_id: int, 
    db: AsyncSession = Depends(get_async_db)
):
    result = await db.execute(select(Game).where(Game.id == game_id))
    game = result.scalar_one_or_none()
    
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
        
    return game


@router.post("/{game_id}/analyze")
async def enqueue_game_analysis(
    game_id: int,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Enqueue a Stockfish analysis task for a single game.

    Per ARCHITECTURE.md §6/§7, this hands off to a Celery worker via Redis;
    the actual engine work happens in `app.tasks.celery_app.analyze_game`.
    """
    result = await db.execute(select(Game).where(Game.id == game_id))
    game = result.scalar_one_or_none()

    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    if game.is_analyzed:
        raise HTTPException(status_code=400, detail="Game has already been analyzed")

    analyze_game.delay(game_id)

    return {"status": "queued", "game_id": game_id}


@router.get("/stats/{player_name}", response_model=PlayerStats)
async def get_player_stats(
    player_name: str,
    time_control: str | None = None,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Aggregated statistics for a single player: ACPL, accuracy by phase and
    error patterns. The three independent aggregations are run concurrently.
    """
    acpl, accuracy_by_phase, errors = await asyncio.gather(
        get_player_acpl(db, player_name, time_control),
        get_accuracy_by_phase(db, player_name),
        get_error_patterns(db, player_name),
    )

    has_acpl = acpl["games_count"] > 0
    has_accuracy = any(phase["moves_count"] > 0 for phase in accuracy_by_phase.values())
    has_errors = bool(errors["errors_by_piece"]) or bool(errors["errors_by_move_number"])

    if not (has_acpl or has_accuracy or has_errors):
        raise HTTPException(status_code=404, detail="Player not found")

    return PlayerStats(
        acpl=acpl,
        accuracy_by_phase=accuracy_by_phase,
        errors=errors,
    )


@router.get("/stats/{player_name}/openings", response_model=list[OpeningStat])
async def get_player_opening_stats(
    player_name: str,
    limit: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Per-opening statistics for a player, top `limit` by number of games.
    """
    rows = await get_opening_stats(db, player_name, limit)

    if not rows:
        raise HTTPException(status_code=404, detail="Player not found")

    return rows


@router.get("/stats/{player_name}/moves", response_model=list[MoveAccuracyStat])
async def get_player_move_stats(
    player_name: str,
    min_games: int = Query(5, ge=1, le=100),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Accuracy metrics for a player aggregated by move number, keeping only move
    numbers reached in at least `min_games` games.
    """
    rows = await get_accuracy_by_move_number(db, player_name, min_games)

    if not rows:
        raise HTTPException(status_code=404, detail="Player not found")

    return rows