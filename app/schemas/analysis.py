from __future__ import annotations

from pydantic import BaseModel


class BatchAnalysisResponse(BaseModel):
    status: str
    player: str
    queued_count: int


class AnalysisProgress(BaseModel):
    player: str
    total: int
    analyzed: int
    pending: int
