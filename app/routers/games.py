import asyncio
import httpx
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.database import get_async_db
from app.models.enums import StandardPerfType
from app.schemas.games import UploadResponse
from app.services.lichess import fetch_games_from_lichess
from app.utils.parser import parse_pgn_text
from app.services.db_manager import bulk_save_games


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
