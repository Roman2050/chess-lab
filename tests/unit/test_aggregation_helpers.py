from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.aggregation.helpers import (
    iter_player_moves,
    resolve_player_color,
)


def _make_game(
    *,
    game_id: int = 1,
    white: str = "alice",
    black: str = "bob",
    analysis_data: dict | None = None,
) -> SimpleNamespace:
    """Minimal Game stand-in for the pure helpers.

    `resolve_player_color` / `iter_player_moves` only read `.id`,
    `.white_player`, `.black_player`, `.analysis_data` — no ORM machinery is
    needed, so SimpleNamespace keeps the unit suite DB-free.
    """
    return SimpleNamespace(
        id=game_id,
        white_player=white,
        black_player=black,
        analysis_data=analysis_data,
    )


@pytest.mark.unit
def test_resolve_player_color_white() -> None:
    game = _make_game(white="alice", black="bob")

    assert resolve_player_color(game, "alice") == "White"


@pytest.mark.unit
def test_resolve_player_color_black() -> None:
    game = _make_game(white="alice", black="bob")

    assert resolve_player_color(game, "bob") == "Black"


@pytest.mark.unit
def test_resolve_player_color_not_found() -> None:
    """Loud failure when the caller forgets to pre-filter by player.

    Silently returning a default would mis-tag every move in the report, so
    the helper crashes with a message that names both the player and the
    offending game id.
    """
    game = _make_game(game_id=42, white="alice", black="bob")

    with pytest.raises(ValueError, match="carol") as excinfo:
        resolve_player_color(game, "carol")

    assert "42" in str(excinfo.value)


@pytest.mark.unit
def test_iter_player_moves_filters_by_color() -> None:
    """Only same-color moves are yielded; other-color moves are dropped."""
    analysis_data = {
        "moves": [
            {"ply": 1, "color": "White", "san": "e4"},
            {"ply": 2, "color": "Black", "san": "e5"},
            {"ply": 3, "color": "White", "san": "Nf3"},
            {"ply": 4, "color": "Black", "san": "Nc6"},
        ]
    }
    game = _make_game(analysis_data=analysis_data)

    white_moves = list(iter_player_moves(game, "White"))

    assert [m["san"] for m in white_moves] == ["e4", "Nf3"]
    assert all(m["color"] == "White" for m in white_moves)


@pytest.mark.unit
def test_iter_player_moves_handles_missing_analysis() -> None:
    """Unanalyzed games yield nothing instead of raising.

    Callers iterate over many games at once; a single unanalyzed row in the
    result set shouldn't blow up the whole aggregation. (`get_player_analyzed_games`
    already filters these out — this is belt-and-braces.)
    """
    game = _make_game(analysis_data=None)

    assert list(iter_player_moves(game, "White")) == []


@pytest.mark.unit
def test_iter_player_moves_handles_missing_moves_key() -> None:
    """`analysis_data` without a "moves" key is treated as empty."""
    game = _make_game(analysis_data={"summary": {"white_acpl": 12}})

    assert list(iter_player_moves(game, "White")) == []
