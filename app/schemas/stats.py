from __future__ import annotations

from pydantic import BaseModel, ConfigDict


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


class WpLossStats(BaseModel):
    player: str
    games_count: int
    total_moves_analyzed: int
    wp_loss: float | None  # середня втрата шансів на хід, %
    wp_loss_by_color: dict[str, float | None]  # {"white": ..., "black": ...}
    wp_loss_by_phase: dict[str, float | None]  # {"opening": ..., ...}


class MoveAccuracyStat(BaseModel):
    move_num: int
    games_count: int
    avg_cp_loss: float
    avg_wp_loss: float | None  # win-probability loss per move, % (Phase 6)
    inaccuracy_rate: float
    mistake_rate: float
    blunder_rate: float

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "move_num": 12,
                    "games_count": 18,
                    "avg_cp_loss": 32.4,
                    "avg_wp_loss": 3.1,
                    "inaccuracy_rate": 11.1,
                    "mistake_rate": 5.6,
                    "blunder_rate": 0.0,
                }
            ]
        }
    )


class OpeningStat(BaseModel):
    opening_name: str
    games_count: int
    wins: int
    draws: int
    losses: int
    win_rate: float
    acpl_in_opening: float | None
    wp_loss_in_opening: float | None  # win-probability loss in opening, % (Phase 6)
    analyzed_games_count: int

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "opening_name": "Sicilian Defense",
                    "games_count": 14,
                    "wins": 7,
                    "draws": 4,
                    "losses": 3,
                    "win_rate": 64.3,
                    "acpl_in_opening": 21.7,
                    "wp_loss_in_opening": 2.4,
                    "analyzed_games_count": 12,
                }
            ]
        }
    )


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

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "acpl": {
                        "player": "DemoPlayer",
                        "games_count": 24,
                        "total_moves_analyzed": 812,
                        "acpl": 31.8,
                        "acpl_by_color": {"white": 28.4, "black": 35.2},
                        "acpl_by_phase": {
                            "opening": 18.2,
                            "middlegame": 36.7,
                            "endgame": 29.1,
                        },
                    },
                    "accuracy_by_phase": {
                        "opening": {
                            "acpl": 18.2,
                            "inaccuracy_rate": 7.4,
                            "mistake_rate": 2.1,
                            "blunder_rate": 0.5,
                            "moves_count": 326,
                        }
                    },
                    "errors": {
                        "errors_by_piece": [
                            {
                                "piece": "N",
                                "piece_name": "Knight",
                                "error_count": 9,
                                "error_pct": 28.1,
                            }
                        ],
                        "errors_by_move_number": [{"move_num": 18, "error_count": 4}],
                    },
                }
            ]
        }
    )
