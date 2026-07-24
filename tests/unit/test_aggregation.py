from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.aggregation.accuracy import (
    get_accuracy_by_move_number,
    get_accuracy_by_phase,
)
from app.services.aggregation.acpl import get_player_acpl
from app.services.aggregation.errors import get_error_patterns
from app.services.aggregation.openings import get_opening_stats

PLAYER = "hero"
OPPONENT = "villain"


def _make_move(
    *,
    ply: int,
    move_num: int,
    color: str = "White",
    san: str = "e4",
    piece: str = "P",
    cp_loss: int = 0,
    classification: str = "best",
    phase: str = "opening",
) -> dict:
    """Full per-move dict matching the analysis_data schema (ARCHITECTURE.md §5.3)."""
    return {
        "ply": ply,
        "move_num": move_num,
        "color": color,
        "san": san,
        "piece": piece,
        "cp_loss": cp_loss,
        "classification": classification,
        "phase": phase,
    }


def _make_game(
    *,
    game_id: int,
    white: str = PLAYER,
    black: str = OPPONENT,
    winner: str | None = None,
    opening_name: str | None = "Sicilian Defense",
    is_analyzed: bool = True,
    moves: list[dict] | None = None,
) -> SimpleNamespace:
    """Game stand-in: the aggregation services only read plain attributes,
    so SimpleNamespace keeps the suite ORM- and DB-free."""
    if is_analyzed:
        analysis_data = {
            "summary": {
                "white_acpl": 0,
                "black_acpl": 0,
                "advantage_lost": {"white": False, "black": False},
            },
            "moves": moves or [],
        }
    else:
        analysis_data = None

    return SimpleNamespace(
        id=game_id,
        white_player=white,
        black_player=black,
        winner=winner,
        opening_name=opening_name,
        is_analyzed=is_analyzed,
        analysis_data=analysis_data,
    )


@pytest.fixture
def fake_games():
    """Factory: fake_games(spec, spec, ...) → list of Game stand-ins.

    Each spec is a kwargs dict for `_make_game`; ids are auto-assigned so
    distinct-game counting in the services works out of the box.
    """

    def _build(*specs: dict) -> list[SimpleNamespace]:
        return [_make_game(game_id=i + 1, **spec) for i, spec in enumerate(specs)]

    return _build


@pytest.fixture
def db() -> AsyncMock:
    """AsyncSession stand-in — never touched once the fetch helper is mocked,
    but the services require it as a positional argument."""
    return AsyncMock()


def _patch_analyzed_games(monkeypatch: pytest.MonkeyPatch, module: str, games: list) -> None:
    """Replace `get_player_analyzed_games` in the *consuming* module's namespace."""

    async def _fake(db, player_name, time_control=None):
        return games

    monkeypatch.setattr(
        f"app.services.aggregation.{module}.get_player_analyzed_games", _fake
    )


def _patch_player_games(monkeypatch: pytest.MonkeyPatch, games: list) -> None:
    """Replace `get_player_games` in the openings module's namespace."""

    async def _fake(db, player_name, time_control=None):
        return games

    monkeypatch.setattr("app.services.aggregation.openings.get_player_games", _fake)


def _moves(count: int, *, cp_loss: int, color: str = "White", **overrides) -> list[dict]:
    """`count` player moves with a constant cp_loss, plies/move_nums auto-numbered."""
    offset = 1 if color == "White" else 2
    return [
        _make_move(
            ply=2 * i + offset,
            move_num=i + 1,
            color=color,
            cp_loss=cp_loss,
            **overrides,
        )
        for i in range(count)
    ]


# ═══════════════════════════════════════════════════════════════════
# ACPL
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.unit
async def test_acpl_weighted_by_games_not_moves(monkeypatch, fake_games, db) -> None:
    """Overall ACPL is the mean of per-game ACPLs, not a flat per-move mean.

    Game 1: 10 moves at cp_loss=20 → game ACPL 20.
    Game 2: 50 moves at cp_loss=40 → game ACPL 40.
    Expected (20 + 40) / 2 = 30 — a naive per-move mean would give ≈36.7,
    so this test catches a regression to the flat average.
    """
    games = fake_games(
        {"moves": _moves(10, cp_loss=20)},
        {"moves": _moves(50, cp_loss=40)},
    )
    _patch_analyzed_games(monkeypatch, "acpl", games)

    result = await get_player_acpl(db, PLAYER)

    assert result["acpl"] == 30.0
    assert result["games_count"] == 2
    assert result["total_moves_analyzed"] == 60


@pytest.mark.unit
async def test_acpl_by_color(monkeypatch, fake_games, db) -> None:
    """White and black games are averaged separately."""
    games = fake_games(
        # hero plays White, game ACPL 20
        {"white": PLAYER, "black": OPPONENT, "moves": _moves(10, cp_loss=20)},
        # hero plays Black, game ACPL 40
        {
            "white": OPPONENT,
            "black": PLAYER,
            "moves": _moves(10, cp_loss=40, color="Black"),
        },
    )
    _patch_analyzed_games(monkeypatch, "acpl", games)

    result = await get_player_acpl(db, PLAYER)

    assert result["acpl_by_color"]["white"] == 20.0
    assert result["acpl_by_color"]["black"] == 40.0
    assert result["acpl"] == 30.0


@pytest.mark.unit
async def test_acpl_by_phase_handles_missing_phase(monkeypatch, fake_games, db) -> None:
    """A quick-mate game with only opening moves: middlegame/endgame are None,
    but the full structure is returned without raising."""
    games = fake_games({"moves": _moves(6, cp_loss=30, phase="opening")})
    _patch_analyzed_games(monkeypatch, "acpl", games)

    result = await get_player_acpl(db, PLAYER)

    assert result["acpl_by_phase"]["opening"] == 30.0
    assert result["acpl_by_phase"]["middlegame"] is None
    assert result["acpl_by_phase"]["endgame"] is None
    assert set(result["acpl_by_phase"]) == {"opening", "middlegame", "endgame"}


@pytest.mark.unit
async def test_acpl_clamps_mate_inflated_moves(monkeypatch, fake_games, db) -> None:
    """Legacy rows with raw mate cp_loss (10050) are clamped at read time.

    A game analysed before the source cap can still carry a ~10000 cp_loss for
    a walk-into-mate move. Without the read-time clamp in `iter_player_moves`
    the per-game ACPL would explode past 1000; with it, that move counts as
    CP_LOSS_CAP (1000) instead.

        9 quiet moves at cp_loss=10 + 1 mate move at cp_loss=10050
        clamped game ACPL = (9*10 + 1000) / 10 = 109.0
    """
    moves = _moves(9, cp_loss=10)
    moves.append(
        _make_move(ply=19, move_num=10, cp_loss=10050, classification="blunder")
    )
    games = fake_games({"moves": moves})
    _patch_analyzed_games(monkeypatch, "acpl", games)

    result = await get_player_acpl(db, PLAYER)

    assert result["acpl"] == 109.0


@pytest.mark.unit
async def test_acpl_empty_player(monkeypatch, db) -> None:
    """No analyzed games: zero counts, every metric None, shape intact."""
    _patch_analyzed_games(monkeypatch, "acpl", [])

    result = await get_player_acpl(db, PLAYER)

    assert result["games_count"] == 0
    assert result["total_moves_analyzed"] == 0
    assert result["acpl"] is None
    assert result["acpl_by_color"] == {"white": None, "black": None}
    assert result["acpl_by_phase"] == {
        "opening": None,
        "middlegame": None,
        "endgame": None,
    }


# ═══════════════════════════════════════════════════════════════════
# Accuracy
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.unit
async def test_accuracy_by_phase_separate_error_rates(
    monkeypatch, fake_games, db
) -> None:
    """1 inaccuracy + 1 mistake + 1 blunder + 7 good out of 10 opening moves
    must produce three *separate* 10% rates — not a fused error_rate."""
    classifications = ["inaccuracy", "mistake", "blunder"] + ["good"] * 7
    moves = [
        _make_move(
            ply=2 * i + 1,
            move_num=i + 1,
            cp_loss=60,
            classification=cls,
            phase="opening",
        )
        for i, cls in enumerate(classifications)
    ]
    games = fake_games({"moves": moves})
    _patch_analyzed_games(monkeypatch, "accuracy", games)

    result = await get_accuracy_by_phase(db, PLAYER)

    opening = result["opening"]
    assert opening["inaccuracy_rate"] == 10.0
    assert opening["mistake_rate"] == 10.0
    assert opening["blunder_rate"] == 10.0
    assert opening["moves_count"] == 10
    assert "error_rate" not in opening


@pytest.mark.unit
async def test_accuracy_by_move_number_min_games_filter(
    monkeypatch, fake_games, db
) -> None:
    """move 15 appears in 10 games, move 40 in only 3 — min_games decides
    whether the rare move number survives."""
    specs = []
    for i in range(10):
        moves = [_make_move(ply=29, move_num=15, cp_loss=10)]
        if i < 3:
            moves.append(_make_move(ply=79, move_num=40, cp_loss=10, phase="endgame"))
        specs.append({"moves": moves})
    games = fake_games(*specs)
    _patch_analyzed_games(monkeypatch, "accuracy", games)

    strict = await get_accuracy_by_move_number(db, PLAYER, min_games=5)
    assert [row["move_num"] for row in strict] == [15]

    relaxed = await get_accuracy_by_move_number(db, PLAYER, min_games=2)
    assert [row["move_num"] for row in relaxed] == [15, 40]


@pytest.mark.unit
async def test_accuracy_by_move_number_no_phase_field(
    monkeypatch, fake_games, db
) -> None:
    """Rows deliberately carry no "phase" key — the same move number can land
    in different phases across games (see Chat 5 / accuracy.py docstring)."""
    games = fake_games(
        {
            "moves": [
                _make_move(ply=9, move_num=5, cp_loss=10, phase="opening"),
                _make_move(ply=49, move_num=25, cp_loss=10, phase="middlegame"),
            ]
        }
    )
    _patch_analyzed_games(monkeypatch, "accuracy", games)

    rows = await get_accuracy_by_move_number(db, PLAYER, min_games=1)

    assert rows
    assert all("phase" not in row for row in rows)


@pytest.mark.unit
async def test_accuracy_by_move_number_includes_wp_loss(
    monkeypatch, fake_games, db
) -> None:
    """Moves carrying evals produce an avg_wp_loss (float, >= 0) without
    dropping the pre-existing avg_cp_loss field (contract stays additive)."""
    games = fake_games(
        {
            "moves": [
                {
                    **_make_move(ply=1, move_num=1, cp_loss=60),
                    "eval_before": 100,
                    "eval_after": -100,
                },
            ]
        }
    )
    _patch_analyzed_games(monkeypatch, "accuracy", games)

    rows = await get_accuracy_by_move_number(db, PLAYER, min_games=1)

    assert rows
    row = rows[0]
    assert "avg_cp_loss" in row
    assert isinstance(row["avg_wp_loss"], float)
    assert row["avg_wp_loss"] >= 0


@pytest.mark.unit
async def test_accuracy_by_move_number_excludes_decided_position_wp(
    monkeypatch, fake_games, db
) -> None:
    """A decided-position move keeps the row but contributes no WP value."""
    games = fake_games(
        {
            "moves": [
                {
                    **_make_move(ply=1, move_num=1, cp_loss=60),
                    "eval_before": 1_000,
                    "eval_after": 900,
                },
            ]
        }
    )
    _patch_analyzed_games(monkeypatch, "accuracy", games)

    rows = await get_accuracy_by_move_number(db, PLAYER, min_games=1)

    assert rows[0]["avg_cp_loss"] == 60.0
    assert rows[0]["avg_wp_loss"] is None


@pytest.mark.unit
async def test_accuracy_by_move_number_wp_none_without_evals(
    monkeypatch, fake_games, db
) -> None:
    """Moves lacking evals → avg_wp_loss is None, but the row is still emitted
    (avg_cp_loss keeps the row alive)."""
    games = fake_games({"moves": [_make_move(ply=1, move_num=1, cp_loss=60)]})
    _patch_analyzed_games(monkeypatch, "accuracy", games)

    rows = await get_accuracy_by_move_number(db, PLAYER, min_games=1)

    assert rows
    assert rows[0]["avg_wp_loss"] is None
    assert "avg_cp_loss" in rows[0]


@pytest.mark.unit
async def test_accuracy_by_move_number_sorted(monkeypatch, fake_games, db) -> None:
    """Rows come back sorted by move_num ASC regardless of input order."""
    games = fake_games(
        {
            "moves": [
                _make_move(ply=59, move_num=30, cp_loss=10, phase="endgame"),
                _make_move(ply=9, move_num=5, cp_loss=10),
                _make_move(ply=23, move_num=12, cp_loss=10, phase="middlegame"),
            ]
        },
        {
            "moves": [
                _make_move(ply=41, move_num=21, cp_loss=10, phase="middlegame"),
                _make_move(ply=3, move_num=2, cp_loss=10),
            ]
        },
    )
    _patch_analyzed_games(monkeypatch, "accuracy", games)

    rows = await get_accuracy_by_move_number(db, PLAYER, min_games=1)

    move_nums = [row["move_num"] for row in rows]
    assert move_nums == sorted(move_nums)
    assert move_nums == [2, 5, 12, 21, 30]


# ═══════════════════════════════════════════════════════════════════
# Openings
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.unit
async def test_opening_stats_win_rate_rounding(monkeypatch, fake_games, db) -> None:
    """10 Sicilian games as White: 5 wins, 3 draws, 2 losses → win_rate 50.0."""
    winners = ["White"] * 5 + ["Draw"] * 3 + ["Black"] * 2
    games = fake_games(
        *(
            {"winner": winner, "opening_name": "Sicilian Defense", "is_analyzed": False}
            for winner in winners
        )
    )
    _patch_player_games(monkeypatch, games)

    rows = await get_opening_stats(db, PLAYER)

    assert len(rows) == 1
    row = rows[0]
    assert row["opening_name"] == "Sicilian Defense"
    assert (row["wins"], row["draws"], row["losses"]) == (5, 3, 2)
    assert row["win_rate"] == 50.0


@pytest.mark.unit
async def test_opening_stats_acpl_only_from_analyzed(
    monkeypatch, fake_games, db
) -> None:
    """acpl_in_opening uses only the 3 analyzed games; the 2 unanalyzed ones
    still count toward games_count."""
    analyzed_specs = [
        {
            "winner": "White",
            "is_analyzed": True,
            "moves": [_make_move(ply=1, move_num=1, cp_loss=cp, phase="opening")],
        }
        for cp in (10, 20, 30)
    ]
    unanalyzed_specs = [{"winner": "White", "is_analyzed": False} for _ in range(2)]
    games = fake_games(*analyzed_specs, *unanalyzed_specs)
    _patch_player_games(monkeypatch, games)

    rows = await get_opening_stats(db, PLAYER)

    assert len(rows) == 1
    row = rows[0]
    assert row["games_count"] == 5
    assert row["analyzed_games_count"] == 3
    assert row["acpl_in_opening"] == 20.0  # (10 + 20 + 30) / 3


@pytest.mark.unit
async def test_opening_stats_sorted_and_limited(monkeypatch, fake_games, db) -> None:
    """15 distinct openings, limit=10 → the 10 most frequent, descending."""
    specs = []
    for i in range(1, 16):  # "Opening 01" played once ... "Opening 15" played 15 times
        specs.extend(
            {
                "winner": "White",
                "opening_name": f"Opening {i:02d}",
                "is_analyzed": False,
            }
            for _ in range(i)
        )
    games = fake_games(*specs)
    _patch_player_games(monkeypatch, games)

    rows = await get_opening_stats(db, PLAYER, limit=10)

    assert len(rows) == 10
    counts = [row["games_count"] for row in rows]
    assert counts == sorted(counts, reverse=True)
    assert counts == list(range(15, 5, -1))
    assert rows[0]["opening_name"] == "Opening 15"


# ═══════════════════════════════════════════════════════════════════
# Error patterns
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.unit
async def test_error_patterns_piece_mapping(monkeypatch, fake_games, db) -> None:
    """Piece codes map to human names; errors_by_piece sorted by count DESC."""
    moves = [
        _make_move(
            ply=2 * i + 1,
            move_num=i + 1,
            piece="Q",
            cp_loss=150,
            classification="mistake",
        )
        for i in range(3)
    ]
    moves.append(
        _make_move(ply=9, move_num=5, piece="N", cp_loss=400, classification="blunder")
    )
    games = fake_games({"moves": moves})
    _patch_analyzed_games(monkeypatch, "errors", games)

    result = await get_error_patterns(db, PLAYER)

    by_piece = result["errors_by_piece"]
    assert [row["piece_name"] for row in by_piece] == ["Queen", "Knight"]
    assert [row["error_count"] for row in by_piece] == [3, 1]
    assert by_piece[0]["error_pct"] == 75.0


@pytest.mark.unit
async def test_error_patterns_top_10_move_numbers(monkeypatch, fake_games, db) -> None:
    """15 distinct move numbers with errors → only the top 10 are returned."""
    moves = [
        _make_move(
            ply=2 * num - 1,
            move_num=num,
            piece="P",
            cp_loss=120,
            classification="mistake",
        )
        for num in range(1, 16)
    ]
    games = fake_games({"moves": moves})
    _patch_analyzed_games(monkeypatch, "errors", games)

    result = await get_error_patterns(db, PLAYER)

    assert len(result["errors_by_move_number"]) == 10


@pytest.mark.unit
async def test_error_patterns_empty(monkeypatch, fake_games, db) -> None:
    """No error-classified moves → both lists come back empty."""
    games = fake_games(
        {"moves": _moves(8, cp_loss=5, classification="best")},
    )
    _patch_analyzed_games(monkeypatch, "errors", games)

    result = await get_error_patterns(db, PLAYER)

    assert result["errors_by_piece"] == []
    assert result["errors_by_move_number"] == []
