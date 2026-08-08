from datetime import date
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class UploadStats(BaseModel):
    saved_new: int = Field(description="Number of new games added to the database")
    total_processed: int = Field(description="Total number of parsed games processed")


class UploadResponse(BaseModel):
    message: str = Field(description="Import outcome")
    stats: UploadStats

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "message": "File demo-games.pgn successfully processed",
                    "stats": {"saved_new": 12, "total_processed": 12},
                }
            ]
        }
    )


class SortOrder(str, Enum):
    asc = "asc"
    desc = "desc"


class GameSummary(BaseModel):
    id: int
    unique_id: str
    white_player: str
    black_player: str
    result: str
    winner: str | None = None
    opening_name: str | None = None
    time_control: str | None = None
    date_played: date | None = None

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": 42,
                    "unique_id": "demo-game-0042",
                    "white_player": "DemoPlayer",
                    "black_player": "Opponent-042",
                    "result": "1-0",
                    "winner": "White",
                    "opening_name": "Sicilian Defense",
                    "time_control": "600+5",
                    "date_played": "2026-01-15",
                }
            ]
        },
    )


class GameDetail(GameSummary):
    pgn_content: str
    is_analyzed: bool = False
    analysis_data: Any | None = None

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": 42,
                    "unique_id": "demo-game-0042",
                    "white_player": "DemoPlayer",
                    "black_player": "Opponent-042",
                    "result": "1-0",
                    "winner": "White",
                    "opening_name": "Sicilian Defense",
                    "time_control": "600+5",
                    "date_played": "2026-01-15",
                    "pgn_content": "1. e4 c5 2. Nf3 d6 3. d4 cxd4",
                    "is_analyzed": True,
                    "analysis_data": {
                        "summary": {
                            "white_acpl": 24.5,
                            "black_acpl": 38.2,
                            "advantage_lost": {"white": False, "black": True},
                        },
                        "moves": [],
                    },
                }
            ]
        },
    )


class PaginatedGames(BaseModel):
    total_count: int = Field(description="Total number of games matching the filters")
    limit: int
    offset: int
    items: list[GameSummary]

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "total_count": 1,
                    "limit": 50,
                    "offset": 0,
                    "items": [
                        {
                            "id": 42,
                            "unique_id": "demo-game-0042",
                            "white_player": "DemoPlayer",
                            "black_player": "Opponent-042",
                            "result": "1-0",
                            "winner": "White",
                            "opening_name": "Sicilian Defense",
                            "time_control": "600+5",
                            "date_played": "2026-01-15",
                        }
                    ],
                }
            ]
        }
    )
