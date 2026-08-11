import asyncio

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_async_db
from app.models.db import Game
from app.models.enums import StandardPerfType
from app.schemas.analysis import AnalysisQueueResponse
from app.schemas.games import GameDetail, PaginatedGames, SortOrder, UploadResponse
from app.schemas.stats import (
    MoveAccuracyStat,
    OpeningStat,
    PlayerStats,
)
from app.security import (
    require_analysis_quota,
    require_lichess_import_quota,
    require_pgn_upload_quota,
)
from app.services.aggregation.accuracy import (
    compute_accuracy_by_phase,
    get_accuracy_by_move_number,
)
from app.services.aggregation.acpl import compute_player_acpl
from app.services.aggregation.errors import compute_error_patterns
from app.services.aggregation.helpers import get_player_analyzed_games
from app.services.aggregation.openings import get_opening_stats
from app.services.db_manager import bulk_save_games
from app.services.game_queries import get_filtered_games
from app.services.lichess import (
    LichessBusyError,
    LichessConfigurationError,
    LichessCoordinationError,
    LichessProtocolError,
    LichessRateLimitedError,
    LichessUnavailableError,
    LichessUserNotFoundError,
    fetch_games_from_lichess,
)
from app.tasks.celery_app import analyze_game
from app.utils.parser import parse_pgn_text

MAX_UPLOAD_BYTES = 20 * 1024 * 1024

router = APIRouter(prefix="/games")

OPERATOR_ERROR_RESPONSES = {
    401: {"description": "Missing or invalid operator API key."},
    429: {
        "description": "Operation quota exhausted. Retry after the number of seconds in `Retry-After`."
    },
    503: {"description": "A required coordination or queue service is unavailable."},
}


@router.post(
    "/lichess/{username}",
    response_model=UploadResponse,
    dependencies=[Depends(require_lichess_import_quota)],
    tags=["Operator Imports"],
    summary="Import a player's Lichess games",
    description=(
        "**Operator-only.** Fetch and persist a bounded Lichess PGN export. Only one "
        "Lichess request may run across the deployment at a time. The endpoint does "
        "not retry upstream failures; honor `Retry-After` on `429`."
    ),
    response_description="Import outcome and number of games persisted.",
    responses={
        **OPERATOR_ERROR_RESPONSES,
        404: {"description": "The Lichess user does not exist."},
        409: {"description": "Another deployment-wide Lichess import is active."},
        502: {"description": "Lichess returned an invalid or unsupported response."},
    },
)
async def load_from_lichess(
    username: str,
    max_games: int = Query(50, ge=1, le=50, description="Number of games to upload (max 50)"),
    perf_type: StandardPerfType | None = None,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Fetches games from the Lichess API for the specified user.
    """
    try:
        raw_pgn = await fetch_games_from_lichess(username, max_games, perf_type)
    except LichessBusyError:
        raise HTTPException(
            status_code=409,
            detail="Lichess import is already in progress",
        ) from None
    except LichessRateLimitedError as exc:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after is not None else None
        raise HTTPException(
            status_code=429,
            detail="Lichess rate limit is active, retry later",
            headers=headers,
        ) from None
    except LichessUserNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Lichess user not found",
        ) from None
    except (LichessConfigurationError, LichessCoordinationError):
        raise HTTPException(
            status_code=503,
            detail="Lichess integration is unavailable",
        ) from None
    except LichessUnavailableError:
        raise HTTPException(
            status_code=503,
            detail="Lichess is temporarily unavailable",
        ) from None
    except LichessProtocolError:
        raise HTTPException(
            status_code=502,
            detail="Invalid response from Lichess",
        ) from None

    parsed_games = await asyncio.to_thread(parse_pgn_text, raw_pgn)

    if not parsed_games:
        return UploadResponse(
            message=f"No standard games found for the user {username}.",
            stats={"saved_new": 0, "total_processed": 0},
        )

    stats = await bulk_save_games(db, parsed_games)

    return UploadResponse(
        message=f"Games from Lichess have been successfully processed for {username}", stats=stats
    )


@router.post(
    "/upload",
    response_model=UploadResponse,
    dependencies=[Depends(require_pgn_upload_quota)],
    tags=["Operator Imports"],
    summary="Upload a PGN file",
    description=(
        "**Operator-only.** Parse and persist standard games from one UTF-8 `.pgn` "
        "file. The upload is limited to 20 MB and `MAX_UPLOAD_GAMES`; duplicate "
        "games are ignored by their stable identifier."
    ),
    response_description="Upload outcome and number of games persisted.",
    responses={
        **OPERATOR_ERROR_RESPONSES,
        400: {
            "description": "Missing filename, wrong extension, unreadable file, or invalid UTF-8."
        },
        413: {"description": "The byte-size or parsed-game limit was exceeded."},
    },
)
async def upload_pgn_file(file: UploadFile = File(...), db: AsyncSession = Depends(get_async_db)):
    """
    Loads games from a standard .pgn file.
    """
    if not file.filename or not file.filename.strip():
        raise HTTPException(status_code=400, detail="Filename is required")

    # File format validation
    if not file.filename.lower().endswith(".pgn"):
        raise HTTPException(
            status_code=400, detail="Only files with the .pgn extension may be uploaded"
        )

    # Check size before reading into memory if provided by server/client
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File size exceeds maximum limit of 20MB")

    try:
        # Read the file into memory
        content = await file.read()
    except Exception:
        raise HTTPException(status_code=400, detail="Unable to read uploaded file") from None

    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File size exceeds maximum limit of 20MB")

    try:
        raw_pgn = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400, detail="File encoding error. UTF-8 is expected."
        ) from None

    parsed_games = await asyncio.to_thread(parse_pgn_text, raw_pgn)

    if len(parsed_games) > settings.MAX_UPLOAD_GAMES:
        raise HTTPException(
            status_code=413,
            detail=f"PGN file exceeds the limit of {settings.MAX_UPLOAD_GAMES} games",
        )

    if not parsed_games:
        return UploadResponse(
            message="No valid standard games were found in the file.",
            stats={"saved_new": 0, "total_processed": 0},
        )

    stats = await bulk_save_games(db, parsed_games)

    return UploadResponse(message=f"File {file.filename} successfully processed", stats=stats)


@router.get(
    "",
    response_model=PaginatedGames,
    tags=["Games"],
    summary="List imported games",
    description=(
        "**Public read.** Return a filtered page of lightweight game summaries. "
        "The list intentionally excludes PGN text and analysis payloads."
    ),
    response_description="A page of game summaries and pagination metadata.",
)
async def get_games_list(
    limit: int = Query(50, ge=1, le=100, description="Number of games on the page"),
    offset: int = Query(0, ge=0, description="Skip elements (for pages)"),
    sort_order: SortOrder = Query(SortOrder.desc, description="Sort (newest or oldest)"),
    player_name: str | None = Query(
        None, description="Filter by player nickname (played with white or black)"
    ),
    winner: str | None = Query(None, description="Filter by winner (White, Black, Draw)"),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get a list of games with pagination and filters (without heavy PGN text).
    """
    total, games = await get_filtered_games(db, limit, offset, sort_order, player_name, winner)

    return PaginatedGames(
        total_count=total,
        limit=limit,
        offset=offset,
        items=games,  # SQLAlchemy models will be automatically converted to GameSummary
    )


@router.get(
    "/{game_id}",
    response_model=GameDetail,
    tags=["Games"],
    summary="Read one game",
    description=(
        "**Public read.** Return full stored game details, including clean PGN and "
        "the analysis payload when analysis has completed."
    ),
    response_description="The requested game and its available analysis data.",
    responses={404: {"description": "Game not found."}},
)
async def get_game_by_id(game_id: int, db: AsyncSession = Depends(get_async_db)):
    result = await db.execute(select(Game).where(Game.id == game_id))
    game = result.scalar_one_or_none()

    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    return game


@router.post(
    "/{game_id}/analyze",
    response_model=AnalysisQueueResponse,
    dependencies=[Depends(require_analysis_quota)],
    tags=["Analysis"],
    summary="Queue analysis for one game",
    description=(
        "**Operator-only.** Publish one Stockfish analysis task to the Celery "
        "`analysis` queue. Work continues asynchronously; poll the player's analysis "
        "status endpoint for database-backed progress."
    ),
    response_description="Confirmation that the game was queued.",
    responses={
        **OPERATOR_ERROR_RESPONSES,
        400: {"description": "The game has already been analyzed."},
        404: {"description": "Game not found."},
    },
)
async def enqueue_game_analysis(
    game_id: int,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Enqueue a Stockfish analysis task for a single game.

    Per ARCHITECTURE.md §6/§7, this hands off to a Celery worker via Redis;
    the actual engine work happens in `app.tasks.celery_app.analyze_game`.

    The `is_analyzed` check below is a fast 400 for an obvious mistake, not the
    concurrency guard — that is the atomic claim inside the task.
    """
    result = await db.execute(select(Game).where(Game.id == game_id))
    game = result.scalar_one_or_none()

    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    if game.is_analyzed:
        raise HTTPException(status_code=400, detail="Game has already been analyzed")

    analyze_game.delay(game_id)

    return {"status": "queued", "game_id": game_id}


@router.get(
    "/stats/{player_name}",
    response_model=PlayerStats,
    tags=["Player Statistics"],
    summary="Read a player's aggregate statistics",
    description=(
        "**Public read.** Compute deterministic ACPL, phase accuracy, and error "
        "patterns from the player's analyzed games. Player matching is case-insensitive."
    ),
    response_description="Aggregate move-quality and error-pattern statistics.",
    responses={404: {"description": "No analyzed games were found for the player."}},
)
async def get_player_stats(
    player_name: str,
    time_control: str | None = None,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Aggregated statistics for a single player: ACPL, accuracy by phase and
    error patterns. All three read the same analyzed-games set, so we fetch it
    once and run the (pure, in-memory) aggregations on it — sharing a single
    AsyncSession across concurrent tasks is unsafe and also re-queried the same
    rows three times.
    """
    games = await get_player_analyzed_games(db, player_name, time_control)

    acpl = compute_player_acpl(games, player_name)
    accuracy_by_phase = compute_accuracy_by_phase(games, player_name)
    errors = compute_error_patterns(games, player_name)

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


@router.get(
    "/stats/{player_name}/openings",
    response_model=list[OpeningStat],
    tags=["Player Statistics"],
    summary="Read a player's opening statistics",
    description=(
        "**Public read.** Return the most-played openings with results, opening ACPL, "
        "and live-position win-probability loss."
    ),
    response_description="Opening statistics ordered by game count.",
    responses={404: {"description": "No games were found for the player."}},
)
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


@router.get(
    "/stats/{player_name}/moves",
    response_model=list[MoveAccuracyStat],
    tags=["Player Statistics"],
    summary="Read accuracy by move number",
    description=(
        "**Public read.** Aggregate centipawn and win-probability loss by move number, "
        "retaining only moves reached in at least `min_games` games."
    ),
    response_description="Per-move-number accuracy and error rates.",
    responses={404: {"description": "No qualifying analyzed games were found."}},
)
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
