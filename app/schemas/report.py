from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from app.schemas.stats import AcplStats, ErrorPatterns, OpeningStat, PhaseStats


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
    acpl: AcplStats
    accuracy_by_phase: dict[str, PhaseStats]  # opening / middlegame / endgame
    openings: list[OpeningStat]
    errors: ErrorPatterns
    insights: ReportInsights
