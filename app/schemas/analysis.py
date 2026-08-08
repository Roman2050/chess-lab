from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class AnalysisQueueResponse(BaseModel):
    status: str
    game_id: int

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"status": "queued", "game_id": 42}]}
    )


class BatchAnalysisResponse(BaseModel):
    status: str
    player: str
    queued_count: int

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"status": "queued", "player": "DemoPlayer", "queued_count": 10}
            ]
        }
    )


class AnalysisProgress(BaseModel):
    player: str
    total: int
    analyzed: int
    pending: int

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "player": "DemoPlayer",
                    "total": 30,
                    "analyzed": 24,
                    "pending": 6,
                }
            ]
        }
    )
