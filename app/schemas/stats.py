from __future__ import annotations

from pydantic import BaseModel


class PhaseStats(BaseModel):
    acpl: float | None
    inaccuracy_rate: float | None
    mistake_rate: float | None
    blunder_rate: float | None
    moves_count: int


class AcplStats(BaseModel):
    player: str
    games_count: int
    total_moves_analyzed: int
    acpl: float | None
    acpl_by_color: dict[str, float | None]  # {"white": ..., "black": ...}
    acpl_by_phase: dict[str, float | None]  # {"opening": ..., ...}


class MoveAccuracyStat(BaseModel):
    move_num: int
    games_count: int
    avg_cp_loss: float
    inaccuracy_rate: float
    mistake_rate: float
    blunder_rate: float


class OpeningStat(BaseModel):
    opening_name: str
    games_count: int
    wins: int
    draws: int
    losses: int
    win_rate: float
    acpl_in_opening: float | None
    analyzed_games_count: int


class ErrorByPiece(BaseModel):
    piece: str
    piece_name: str
    error_count: int
    error_pct: float


class ErrorByMoveNumber(BaseModel):
    move_num: int
    error_count: int


class ErrorPatterns(BaseModel):
    errors_by_piece: list[ErrorByPiece]
    errors_by_move_number: list[ErrorByMoveNumber]


class PlayerStats(BaseModel):
    acpl: AcplStats
    accuracy_by_phase: dict[str, PhaseStats]  # opening/middlegame/endgame
    errors: ErrorPatterns
