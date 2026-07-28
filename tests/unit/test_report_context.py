from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.schemas.report import ReportContext
from app.schemas.stats import WpLossStats
from app.services import report_context as rc
from app.services.report_context import (
    COLOR_BIAS_THRESHOLD,
    DOMINANT_ERROR_PCT,
    MIN_OPENING_GAMES,
    PHASE_WP_BIAS_THRESHOLD,
    build_report_context,
    derive_insights,
)

PLAYER = "hero"
OPPONENT = "villain"


def _wp(
    *,
    overall: float | None = 3.0,
    white: float | None = None,
    black: float | None = None,
    opening: float | None = None,
    middlegame: float | None = None,
    endgame: float | None = None,
) -> dict:
    return {
        "player": PLAYER,
        "games_count": 5,
        "total_moves_analyzed": 100,
        "wp_loss": overall,
        "wp_loss_by_color": {"white": white, "black": black},
        "wp_loss_by_phase": {
            "opening": opening,
            "middlegame": middlegame,
            "endgame": endgame,
        },
    }


def _phase(acpl: float | None, moves_count: int) -> dict:
    return {
        "acpl": acpl,
        "inaccuracy_rate": 0.0,
        "mistake_rate": 0.0,
        "blunder_rate": 0.0,
        "moves_count": moves_count,
    }


def _accuracy(
    opening: dict | None = None,
    middlegame: dict | None = None,
    endgame: dict | None = None,
) -> dict:
    return {
        "opening": opening or _phase(15.0, 40),
        "middlegame": middlegame or _phase(45.0, 50),
        "endgame": endgame or _phase(None, 0),
    }


def _errors(by_piece: list[dict] | None = None, by_move: list[dict] | None = None) -> dict:
    return {
        "errors_by_piece": by_piece if by_piece is not None else [],
        "errors_by_move_number": by_move if by_move is not None else [],
    }


def _opening(name: str, games_count: int, win_rate: float) -> dict:
    return {
        "opening_name": name,
        "games_count": games_count,
        "wins": 0,
        "draws": 0,
        "losses": 0,
        "win_rate": win_rate,
        "acpl_in_opening": None,
        "wp_loss_in_opening": None,
        "analyzed_games_count": 0,
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "wp_value, expected",
    [
        (None, "inconsistent"),
        (0.0, "strong"),
        (2.5, "strong"),
        (2.6, "solid"),
        (4.0, "solid"),
        (4.1, "inconsistent"),
        (6.0, "inconsistent"),
        (6.1, "weak"),
        (15.0, "weak"),
    ],
)
def test_overall_skill_wp_buckets(wp_value, expected) -> None:
    insights = derive_insights(_wp(overall=wp_value), _accuracy(), _errors(), [])
    assert insights.overall_skill == expected


@pytest.mark.unit
def test_weakest_phase_is_max_wp() -> None:
    wp = _wp(opening=1.0, middlegame=5.0, endgame=3.0)
    insights = derive_insights(wp, _accuracy(), _errors(), [])
    assert insights.weakest_phase == "middlegame"


@pytest.mark.unit
def test_weakest_strongest_phase_none_when_no_data() -> None:
    wp = _wp(opening=None, middlegame=None, endgame=None)
    insights = derive_insights(wp, _accuracy(), _errors(), [])
    assert insights.weakest_phase is None
    assert insights.strongest_phase is None


@pytest.mark.unit
def test_strongest_phase_requires_significance() -> None:
    # Best phase leads the next-best by >= threshold → declared strongest.
    clear = derive_insights(
        _wp(opening=1.0, middlegame=1.0 + PHASE_WP_BIAS_THRESHOLD, endgame=6.0),
        _accuracy(),
        _errors(),
        [],
    )
    assert clear.strongest_phase == "opening"

    # Two best phases within the noise margin → no strongest phase.
    close = derive_insights(
        _wp(
            opening=1.0,
            middlegame=1.0 + (PHASE_WP_BIAS_THRESHOLD - 0.1),
            endgame=6.0,
        ),
        _accuracy(),
        _errors(),
        [],
    )
    assert close.strongest_phase is None
    # weakest is unaffected by the significance guard.
    assert close.weakest_phase == "endgame"


@pytest.mark.unit
def test_weaker_color_wp_threshold() -> None:
    below = COLOR_BIAS_THRESHOLD - 0.1
    near = derive_insights(
        _wp(white=3.0, black=3.0 + below), _accuracy(), _errors(), []
    )
    assert near.weaker_color is None

    far = derive_insights(
        _wp(white=3.0, black=3.0 + COLOR_BIAS_THRESHOLD),
        _accuracy(),
        _errors(),
        [],
    )
    assert far.weaker_color == "black"

    missing = derive_insights(_wp(white=3.0, black=None), _accuracy(), _errors(), [])
    assert missing.weaker_color is None


@pytest.mark.unit
def test_derive_insights_dominant_piece_threshold() -> None:
    below = derive_insights(
        _wp(),
        _accuracy(),
        _errors(by_piece=[{"piece": "Q", "piece_name": "Queen",
                           "error_count": 2, "error_pct": DOMINANT_ERROR_PCT - 0.1}]),
        [],
    )
    assert below.dominant_error_piece is None

    above = derive_insights(
        _wp(),
        _accuracy(),
        _errors(by_piece=[{"piece": "Q", "piece_name": "Queen",
                           "error_count": 5, "error_pct": DOMINANT_ERROR_PCT}]),
        [],
    )
    assert above.dominant_error_piece == "Queen"


@pytest.mark.unit
def test_derive_insights_error_hotspot_moves_top_three() -> None:
    by_move = [
        {"move_num": 12, "error_count": 9},
        {"move_num": 7, "error_count": 6},
        {"move_num": 21, "error_count": 4},
        {"move_num": 3, "error_count": 1},
    ]
    insights = derive_insights(_wp(), _accuracy(), _errors(by_move=by_move), [])
    assert insights.error_hotspot_moves == [12, 7, 21]


@pytest.mark.unit
def test_derive_insights_best_worst_openings_min_games() -> None:
    openings = [
        _opening("Sicilian Defense", MIN_OPENING_GAMES, win_rate=70.0),
        _opening("Italian Game", MIN_OPENING_GAMES + 2, win_rate=20.0),
        _opening("French Defense", MIN_OPENING_GAMES - 1, win_rate=100.0),  # too few
    ]
    insights = derive_insights(_wp(), _accuracy(), _errors(), openings)

    assert "French Defense" not in insights.best_openings
    assert insights.best_openings[0] == "Sicilian Defense"
    assert insights.worst_openings[0] == "Italian Game"


def _game(
    *,
    game_id: int,
    winner: str | None = "White",
    opening_name: str | None = "Sicilian Defense",
    is_analyzed: bool = True,
    date_played: date | None = None,
) -> SimpleNamespace:
    moves = [
        {"ply": 1, "move_num": 1, "color": "White", "cp_loss": 30,
         "eval_before": 50, "eval_after": 20,
         "phase": "opening", "classification": "good", "piece": "P"},
    ]
    return SimpleNamespace(
        id=game_id,
        white_player=PLAYER,
        black_player=OPPONENT,
        winner=winner,
        opening_name=opening_name,
        is_analyzed=is_analyzed,
        analysis_data={"moves": moves} if is_analyzed else None,
        date_played=date_played,
    )


@pytest.mark.unit
def test_build_report_context_uses_wp(monkeypatch) -> None:
    games_analyzed = [
        _game(game_id=1, date_played=date(2025, 1, 1)),
        _game(game_id=2, date_played=date(2025, 6, 10)),
    ]
    games_all = games_analyzed + [
        _game(game_id=3, is_analyzed=False, date_played=date(2025, 3, 5)),
    ]

    monkeypatch.setattr(rc, "get_player_games_sync", lambda db, name: games_all)

    ctx = build_report_context(db=None, player_name=PLAYER, language="uk")

    assert isinstance(ctx, ReportContext)
    assert ctx.player == PLAYER
    assert ctx.language == "uk"
    assert ctx.analyzed_games_count == len(games_analyzed)
    assert ctx.total_games_count == len(games_all)
    assert ctx.last_game_played_at == date(2025, 6, 10)
    # Report is now expressed in win-probability loss, not ACPL.
    assert isinstance(ctx.wp, WpLossStats)
    assert ctx.wp.player == PLAYER
    assert not hasattr(ctx, "acpl")
    assert set(ctx.accuracy_by_phase) == {"opening", "middlegame", "endgame"}
    assert ctx.openings  # at least one opening row
    assert ctx.insights is not None


@pytest.mark.unit
def test_report_context_single_fetch(monkeypatch) -> None:
    """One query for the whole context; the analyzed subset is filtered in memory.

    Re-querying it would transfer every analyzed game's `analysis_data` twice —
    the heaviest column on `games` — and hold the task's session for longer.
    """
    games_all = [
        _game(game_id=1, date_played=date(2025, 1, 1)),
        _game(game_id=2, is_analyzed=False, date_played=date(2025, 3, 5)),
    ]
    calls: list[str] = []

    def _fetch_all(db, name):
        calls.append(name)
        return games_all

    monkeypatch.setattr(rc, "get_player_games_sync", _fetch_all)

    ctx = build_report_context(db=None, player_name=PLAYER, language="uk")

    assert calls == [PLAYER]
    assert ctx.total_games_count == 2
    assert ctx.analyzed_games_count == 1
