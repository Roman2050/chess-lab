from pydantic import BaseModel, Field, ConfigDict
from enum import Enum
from typing import Optional, List
from datetime import date

class UploadStats(BaseModel):
    saved_new: int = Field(..., description="Number of new games added to the database")
    total_processed: int = Field(..., description="Total number of moves found in the PGN file")

class UploadResponse(BaseModel):
    message: str = Field(..., description="Transaction Status Notification")
    stats: UploadStats

class SortOrder(str, Enum):
    asc = "asc"
    desc = "desc" 

class GameSummary(BaseModel):
    id: str
    unique_id: str
    white_player: str
    black_player: str
    result: str
    winner: Optional[str] = None
    opening_name: Optional[str] = None
    time_control: Optional[str] = None
    date_played: Optional[date] = None

    model_config = ConfigDict(from_attributes=True)

class GameDetail(GameSummary):
    pgn_content: str

class PaginatedGames(BaseModel):
    total_count: int = Field(description="Total number of games based on these filters")
    limit: int
    offset: int
    items: List[GameSummary]
