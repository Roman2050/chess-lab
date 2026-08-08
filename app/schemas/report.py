from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.stats import ErrorPatterns, OpeningStat, PhaseStats, WpLossStats


class ReportInsights(BaseModel):
    """Deterministic, code-derived conclusions fed to the LLM as narrative hints.

    Every field here is computed in plain Python (see
    :func:`app.services.report_context.derive_insights`) — the model only puts
    these facts into prose, it never derives them itself.
    """

    overall_skill: str  # "strong" | "solid" | "inconsistent" | "weak"
    weakest_phase: str | None  # "opening" | "middlegame" | "endgame"
    strongest_phase: str | None
    weaker_color: str | None  # "white" | "black" | None
    dominant_error_piece: str | None  # piece_name, e.g. "Queen"
    error_hotspot_moves: list[int]  # top move numbers where errors cluster
    best_openings: list[str]  # opening names with the best win-rate
    worst_openings: list[str]


class ReportContext(BaseModel):
    """The full, structured source of truth for one player report.

    Assembled from the Phase 4 aggregations plus derived ``insights``. This is
    what gets rendered into the LLM prompt — the model receives no other data.
    """

    player: str
    language: str
    analyzed_games_count: int
    total_games_count: int
    last_game_played_at: date | None
    wp: WpLossStats
    accuracy_by_phase: dict[str, PhaseStats]  # opening / middlegame / endgame
    openings: list[OpeningStat]
    errors: ErrorPatterns
    insights: ReportInsights


class ReportRequestResponse(BaseModel):
    """Response to ``POST /api/v1/report/{username}`` — the decision outcome."""

    player: str
    language: str
    action: str  # ReportAction value
    message: str
    current_analyzed_games_count: int
    report_games_count: int | None  # snapshot of an existing report (None if absent)
    games_until_next_report: int | None  # how many more analyzed games until refresh

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "player": "DemoPlayer",
                    "language": "en",
                    "action": "up_to_date",
                    "message": "Report is up to date",
                    "current_analyzed_games_count": 24,
                    "report_games_count": 20,
                    "games_until_next_report": 16,
                }
            ]
        }
    )


class ReportResponse(BaseModel):
    """Response to ``GET /api/v1/report/{username}`` — cached text + freshness."""

    player: str
    language: str
    report_text: str
    status: str
    analyzed_games_count: int  # snapshot the report was generated on
    current_analyzed_games_count: int
    is_stale: bool  # delta >= threshold
    created_at: datetime
    updated_at: datetime
    last_game_played_at: datetime | None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "player": "DemoPlayer",
                    "language": "en",
                    "report_text": (
                        "DemoPlayer is most consistent in the opening and should "
                        "prioritize middlegame calculation training."
                    ),
                    "status": "ready",
                    "analyzed_games_count": 24,
                    "current_analyzed_games_count": 24,
                    "is_stale": False,
                    "created_at": "2026-01-16T10:00:00Z",
                    "updated_at": "2026-01-16T10:02:30Z",
                    "last_game_played_at": "2026-01-15T00:00:00Z",
                }
            ]
        }
    )


class ReportStatusResponse(BaseModel):
    """Response to ``GET /api/v1/report/{username}/status`` — state only."""

    player: str
    language: str
    status: str  # "none" | "generating" | "ready" | "failed"
    has_report: bool
    analyzed_games_count: int | None
    current_analyzed_games_count: int
    games_until_next_report: int | None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "player": "DemoPlayer",
                    "language": "en",
                    "status": "ready",
                    "has_report": True,
                    "analyzed_games_count": 24,
                    "current_analyzed_games_count": 24,
                    "games_until_next_report": 20,
                }
            ]
        }
    )
