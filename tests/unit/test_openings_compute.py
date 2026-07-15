from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.aggregation.openings import compute_opening_stats

PLAYER = "hero"
OPPONENT = "villain"


def _make_move(
    *,
    ply: int = 1,
    move_num: int = 1,
    color: str = "White",
    cp_loss: int = 0,
    phase: str = "opening",
) -> dict:
    """Minimal per-move dict — compute_opening_stats only reads color/phase/cp_loss."""
    return {
        "ply": ply,
        "move_num": move_num,
        "color": color,
        "cp_loss": cp_loss,
        "phase": phase,
    }


def _make_game(
    *,
    game_id: int,
    white: str = PLAYER,
    black: str = OPPONENT,
    winner: str | None = None,
    opening_name: str | None = "Sicilian Defense",
    is_analyzed: bool = False,
    moves: list[dict] | None = None,
) -> SimpleNamespace:
    """DB-free Game stand-in: compute_opening_stats only touches plain attributes."""
    analysis_data = {"moves": moves or []} if is_analyzed else None
    return SimpleNamespace(
        id=game_id,
        white_player=white,
        black_player=black,
        winner=winner,
        opening_name=opening_name,
        is_analyzed=is_analyzed,
        analysis_data=analysis_data,
    )


def _games(*specs: dict) -> list[SimpleNamespace]:
    return [_make_game(game_id=i + 1, **spec) for i, spec in enumerate(specs)]


@pytest.mark.unit
def test_compute_opening_stats_win_rate_rounding() -> None:
    """3 wins / 8 games as White → win_rate 37.5 (exercises the .1 rounding)."""
    winners = ["White"] * 3 + ["Draw"] * 2 + ["Black"] * 3
    games = _games(*({"winner": w} for w in winners))

    rows = compute_opening_stats(games, PLAYER)

    assert len(rows) == 1
    row = rows[0]
    assert (row["wins"], row["draws"], row["losses"]) == (3, 2, 3)
    assert row["games_count"] == 8
    assert row["win_rate"] == 37.5


@pytest.mark.unit
def test_compute_opening_stats_acpl_only_from_analyzed() -> None:
    """acpl_in_opening averages opening cp_loss across analyzed games only;
    unanalyzed games still bump games_count but contribute no cp_loss."""
    analyzed = [
        {
            "is_analyzed": True,
            "moves": [_make_move(cp_loss=cp, phase="opening")],
        }
        for cp in (10, 20, 60)
    ]
    unanalyzed = [{"is_analyzed": False} for _ in range(2)]
    games = _games(*analyzed, *unanalyzed)

    rows = compute_opening_stats(games, PLAYER)

    row = rows[0]
    assert row["games_count"] == 5
    assert row["analyzed_games_count"] == 3
    assert row["acpl_in_opening"] == 30.0  # (10 + 20 + 60) / 3


@pytest.mark.unit
def test_compute_opening_stats_acpl_none_without_analyzed() -> None:
    """Openings with no analyzed games report acpl_in_opening=None, not 0."""
    games = _games({"winner": "White", "is_analyzed": False})

    row = compute_opening_stats(games, PLAYER)[0]

    assert row["analyzed_games_count"] == 0
    assert row["acpl_in_opening"] is None


@pytest.mark.unit
def test_opening_wp_loss_present_for_analyzed() -> None:
    """Analyzed opening moves carrying evals → wp_loss_in_opening is a float >= 0."""
    games = _games(
        {
            "winner": "White",
            "is_analyzed": True,
            "moves": [
                {
                    **_make_move(cp_loss=60, phase="opening"),
                    "eval_before": 100,
                    "eval_after": -100,
                },
            ],
        }
    )

    row = compute_opening_stats(games, PLAYER)[0]

    assert isinstance(row["wp_loss_in_opening"], float)
    assert row["wp_loss_in_opening"] >= 0


@pytest.mark.unit
def test_opening_wp_loss_none_without_analyzed() -> None:
    """No analyzed games → wp_loss_in_opening is None (mirrors acpl_in_opening)."""
    games = _games({"winner": "White", "is_analyzed": False})

    row = compute_opening_stats(games, PLAYER)[0]

    assert row["acpl_in_opening"] is None
    assert row["wp_loss_in_opening"] is None


@pytest.mark.unit
def test_compute_opening_stats_sorted_and_limited() -> None:
    """12 distinct openings, limit=10 → top 10 by games_count, descending."""
    specs = []
    for i in range(1, 13):  # "Opening 01" once ... "Opening 12" twelve times
        specs.extend(
            {"winner": "White", "opening_name": f"Opening {i:02d}"} for _ in range(i)
        )
    games = _games(*specs)

    rows = compute_opening_stats(games, PLAYER, limit=10)

    counts = [row["games_count"] for row in rows]
    assert len(rows) == 10
    assert counts == sorted(counts, reverse=True)
    assert counts == list(range(12, 2, -1))
    assert rows[0]["opening_name"] == "Opening 12"


@pytest.mark.unit
def test_compute_opening_stats_drops_null_openings() -> None:
    """Games with NULL/empty opening_name are excluded entirely."""
    games = _games(
        {"winner": "White", "opening_name": None},
        {"winner": "White", "opening_name": ""},
        {"winner": "White", "opening_name": "Sicilian Defense"},
    )

    rows = compute_opening_stats(games, PLAYER)

    assert [row["opening_name"] for row in rows] == ["Sicilian Defense"]
