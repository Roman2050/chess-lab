from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.aggregation.winprob import (
    compute_player_wp_loss,
    move_wp_loss,
    win_prob,
)

PLAYER = "hero"
OPPONENT = "villain"


def _make_move(
    *,
    color: str = "White",
    eval_before: int | None = 0,
    eval_after: int | None = 0,
    phase: str = "opening",
) -> dict:
    """Per-move dict carrying the (raw, White-relative) evals WP reads."""
    move: dict = {"color": color, "phase": phase}
    if eval_before is not None:
        move["eval_before"] = eval_before
    if eval_after is not None:
        move["eval_after"] = eval_after
    return move


def _make_game(
    *,
    game_id: int = 1,
    white: str = PLAYER,
    black: str = OPPONENT,
    moves: list[dict] | None = None,
) -> SimpleNamespace:
    """Game stand-in: aggregation reads only plain attributes → DB-free."""
    return SimpleNamespace(
        id=game_id,
        white_player=white,
        black_player=black,
        analysis_data={"moves": moves or []},
    )


# ═══════════════════════════════════════════════════════════════════
# win_prob
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.unit
def test_win_prob_bounds_and_symmetry() -> None:
    assert win_prob(0) == 50.0
    assert win_prob(100_000) == pytest.approx(100.0, abs=1e-6)
    assert win_prob(-100_000) == pytest.approx(0.0, abs=1e-6)
    for x in (0, 50, 300, 1500, 9000):
        assert win_prob(x) + win_prob(-x) == pytest.approx(100.0, abs=1e-9)


@pytest.mark.unit
def test_win_prob_monotonic() -> None:
    """Більша перевага в cp → не менші шанси."""
    cps = [-2000, -500, -50, 0, 50, 500, 2000]
    probs = [win_prob(cp) for cp in cps]
    assert probs == sorted(probs)


# ═══════════════════════════════════════════════════════════════════
# move_wp_loss
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.unit
def test_move_wp_loss_white_vs_black_sign() -> None:
    """Дзеркальні White/Black кейси дають однаковий (>0) wp_loss."""
    white = _make_move(color="White", eval_before=100, eval_after=-100)
    black = _make_move(color="Black", eval_before=-100, eval_after=100)

    white_loss = move_wp_loss(white)
    black_loss = move_wp_loss(black)

    assert white_loss > 0
    assert black_loss > 0
    assert white_loss == pytest.approx(black_loss)


@pytest.mark.unit
def test_move_wp_loss_improving_move_is_zero() -> None:
    """Хід, що покращує оцінку для сторони → clamped max(0,...) == 0.0."""
    move = _make_move(color="White", eval_before=-100, eval_after=100)
    assert move_wp_loss(move) == 0.0


@pytest.mark.unit
def test_move_wp_loss_mate_saturates() -> None:
    """Сигмоїда насичує мат — wp_loss не «вибухає»."""
    walk_into_mate = _make_move(color="White", eval_before=0, eval_after=-10_000)
    assert move_wp_loss(walk_into_mate) == pytest.approx(50.0, abs=1.0)

    already_won = _make_move(color="White", eval_before=9_000, eval_after=10_000)
    assert move_wp_loss(already_won) == pytest.approx(0.0, abs=1.0)


@pytest.mark.unit
def test_move_wp_loss_missing_evals_returns_none() -> None:
    assert move_wp_loss(_make_move(eval_before=None)) is None
    assert move_wp_loss(_make_move(eval_after=None)) is None
    assert move_wp_loss(_make_move(eval_before=None, eval_after=None)) is None


# ═══════════════════════════════════════════════════════════════════
# compute_player_wp_loss
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.unit
def test_compute_player_wp_loss_overall_and_splits() -> None:
    games = [
        # hero White: two opening moves losing WP.
        _make_game(
            game_id=1,
            white=PLAYER,
            black=OPPONENT,
            moves=[
                _make_move(color="White", eval_before=100, eval_after=-100),
                _make_move(color="White", eval_before=50, eval_after=-50),
                # opponent move ignored by color filter
                _make_move(color="Black", eval_before=0, eval_after=500),
            ],
        ),
        # hero Black: a middlegame move losing WP.
        _make_game(
            game_id=2,
            white=OPPONENT,
            black=PLAYER,
            moves=[
                _make_move(
                    color="Black",
                    eval_before=-100,
                    eval_after=100,
                    phase="middlegame",
                ),
            ],
        ),
    ]

    result = compute_player_wp_loss(games, PLAYER)

    assert result["player"] == PLAYER
    assert result["games_count"] == 2
    assert result["total_moves_analyzed"] == 3
    assert result["wp_loss"] is not None
    assert result["wp_loss"] > 0

    assert result["wp_loss_by_color"]["white"] is not None
    assert result["wp_loss_by_color"]["black"] is not None

    assert result["wp_loss_by_phase"]["opening"] is not None
    assert result["wp_loss_by_phase"]["middlegame"] is not None
    # No endgame moves anywhere → None (not 0).
    assert result["wp_loss_by_phase"]["endgame"] is None


@pytest.mark.unit
def test_compute_player_wp_loss_color_without_games_is_none() -> None:
    """hero only ever played White → black slice stays None."""
    games = [
        _make_game(
            game_id=1,
            white=PLAYER,
            black=OPPONENT,
            moves=[_make_move(color="White", eval_before=100, eval_after=-100)],
        ),
    ]

    result = compute_player_wp_loss(games, PLAYER)

    assert result["wp_loss_by_color"]["white"] is not None
    assert result["wp_loss_by_color"]["black"] is None


@pytest.mark.unit
def test_compute_player_wp_loss_skips_games_without_evals() -> None:
    """A game whose player moves all lack evals doesn't become a per-game point."""
    games = [
        _make_game(
            game_id=1,
            white=PLAYER,
            black=OPPONENT,
            moves=[_make_move(color="White", eval_before=None, eval_after=None)],
        ),
    ]

    result = compute_player_wp_loss(games, PLAYER)

    assert result["games_count"] == 0
    assert result["wp_loss"] is None


@pytest.mark.unit
def test_compute_player_wp_loss_empty_games() -> None:
    result = compute_player_wp_loss([], PLAYER)

    assert result["games_count"] == 0
    assert result["total_moves_analyzed"] == 0
    assert result["wp_loss"] is None
    assert result["wp_loss_by_color"] == {"white": None, "black": None}
    assert result["wp_loss_by_phase"] == {
        "opening": None,
        "middlegame": None,
        "endgame": None,
    }
