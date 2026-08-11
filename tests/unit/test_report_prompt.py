from __future__ import annotations

from datetime import date

import pytest

from app.schemas.report import ReportContext, ReportInsights
from app.schemas.stats import (
    ErrorByMoveNumber,
    ErrorByPiece,
    ErrorPatterns,
    OpeningStat,
    PhaseStats,
    WpLossStats,
)
from app.services.report_prompt import (
    build_messages,
    build_system_prompt,
    render_context_to_prompt,
)

PLAYER = "villain"


def _phase(
    moves_count: int,
    *,
    reached: bool = True,
    inaccuracy: float | None = 5.0,
    mistake: float | None = 2.0,
    blunder: float | None = 1.0,
) -> PhaseStats:
    return PhaseStats(
        acpl=None,
        inaccuracy_rate=inaccuracy if reached else None,
        mistake_rate=mistake if reached else None,
        blunder_rate=blunder if reached else None,
        moves_count=moves_count,
    )


def _opening(
    name: str,
    games_count: int,
    win_rate: float,
    wp_loss_in_opening: float | None = None,
) -> OpeningStat:
    return OpeningStat(
        opening_name=name,
        games_count=games_count,
        wins=0,
        draws=0,
        losses=0,
        win_rate=win_rate,
        acpl_in_opening=None,
        wp_loss_in_opening=wp_loss_in_opening,
        analyzed_games_count=0,
    )


def _context(
    *,
    language: str = "English",
    analyzed: int = 12,
    total: int = 30,
    last_game: date | None = date(2025, 6, 10),
    overall_wp: float | None = 3.4,
    white: float | None = 3.0,
    black: float | None = 3.8,
    wp_by_phase: dict[str, float | None] | None = None,
    accuracy: dict[str, PhaseStats] | None = None,
    openings: list[OpeningStat] | None = None,
    by_piece: list[ErrorByPiece] | None = None,
    by_move: list[ErrorByMoveNumber] | None = None,
    insights: ReportInsights | None = None,
) -> ReportContext:
    if wp_by_phase is None:
        wp_by_phase = {"opening": 1.5, "middlegame": 4.5, "endgame": None}
    wp = WpLossStats(
        player=PLAYER,
        games_count=analyzed,
        total_moves_analyzed=240,
        wp_loss=overall_wp,
        wp_loss_by_color={"white": white, "black": black},
        wp_loss_by_phase=wp_by_phase,
    )
    if accuracy is None:
        accuracy = {
            "opening": _phase(40),
            "middlegame": _phase(50),
            "endgame": _phase(0, reached=False),
        }
    if openings is None:
        openings = [
            _opening("Sicilian Defense", 8, 62.5, wp_loss_in_opening=1.8),
            _opening("Italian Game", 5, 20.0),
        ]
    errors = ErrorPatterns(
        errors_by_piece=by_piece
        if by_piece is not None
        else [ErrorByPiece(piece="Q", piece_name="Queen", error_count=6, error_pct=30.0)],
        errors_by_move_number=by_move
        if by_move is not None
        else [
            ErrorByMoveNumber(move_num=12, error_count=9),
            ErrorByMoveNumber(move_num=7, error_count=6),
        ],
    )
    if insights is None:
        insights = ReportInsights(
            overall_skill="solid",
            weakest_phase="middlegame",
            strongest_phase="opening",
            weaker_color="black",
            dominant_error_piece="Queen",
            error_hotspot_moves=[12, 7],
            best_openings=["Sicilian Defense"],
            worst_openings=["Italian Game"],
        )
    return ReportContext(
        player=PLAYER,
        language=language,
        analyzed_games_count=analyzed,
        total_games_count=total,
        last_game_played_at=last_game,
        wp=wp,
        accuracy_by_phase=accuracy,
        openings=openings,
        errors=errors,
        insights=insights,
    )


@pytest.mark.unit
def test_system_prompt_includes_language() -> None:
    assert "English" in build_system_prompt("English")
    assert "Ukrainian" in build_system_prompt("Ukrainian")


@pytest.mark.unit
def test_render_contains_wp_numbers() -> None:
    ctx = _context()
    text = render_context_to_prompt(ctx)

    assert PLAYER in text
    assert "analyzed 12 of 30" in text
    assert "2025-06-10" in text
    assert "3.4" in text  # overall wp loss
    assert "4.5" in text  # middlegame wp loss (by phase)
    assert "62.5" in text  # leading opening win rate
    assert "1.8" in text  # leading opening wp loss
    assert "Sicilian Defense" in text
    assert "Queen" in text
    assert "<=2.5 strong" in text
    assert ">4-6 inconsistent" in text
    assert ">6 weak" in text


@pytest.mark.unit
def test_render_has_no_acpl_wording() -> None:
    text = render_context_to_prompt(_context()).lower()
    assert "acpl" not in text
    assert "centipawn" not in text


@pytest.mark.unit
def test_render_handles_missing_slices() -> None:
    ctx = _context(
        last_game=None,
        overall_wp=None,
        white=None,
        black=None,
        wp_by_phase={"opening": None, "middlegame": None, "endgame": None},
        accuracy={
            "opening": _phase(0, reached=False),
            "middlegame": _phase(0, reached=False),
            "endgame": _phase(0, reached=False),
        },
        openings=[],
        by_piece=[],
        by_move=[],
        insights=ReportInsights(
            overall_skill="inconsistent",
            weakest_phase=None,
            strongest_phase=None,
            weaker_color=None,
            dominant_error_piece=None,
            error_hotspot_moves=[],
            best_openings=[],
            worst_openings=[],
        ),
    )
    text = render_context_to_prompt(ctx)

    # Phases with no moves are explicitly "no data", never silently faked as 0.
    by_phase_block = text.split("BY PHASE")[1].split("ERROR PROFILE")[0]
    assert by_phase_block.count("no data") == 3
    assert "0" not in by_phase_block

    assert "not enough analyzed games" in text  # overall wp missing
    assert "no data" in text


@pytest.mark.unit
def test_render_is_deterministic() -> None:
    ctx = _context()
    assert render_context_to_prompt(ctx) == render_context_to_prompt(ctx)


@pytest.mark.unit
def test_build_messages_pairs_system_and_user() -> None:
    ctx = _context(language="Ukrainian")
    system, user = build_messages(ctx)

    assert system == build_system_prompt("Ukrainian")
    assert user == render_context_to_prompt(ctx)
    assert "Ukrainian" in system
