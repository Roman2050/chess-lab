from __future__ import annotations

import math

from app.models.db import Game
from app.services.aggregation.helpers import (
    iter_player_moves,
    resolve_player_color,
)

_PHASES: tuple[str, ...] = ("opening", "middlegame", "endgame")

# Logistic constant (Lichess calibration) — converts centipawn advantage to win probability.
# Extracted as a constant; calibrated in Phase 6 Chat 5.
WP_SCALE = 0.00368208

# Calibrated window for "live" positions, based on real data. Moves where the
# moving side's win probability is already <= 20% or >= 90% are excluded from
# WP averages: the saturated sigmoid would otherwise dilute the metric with near-zero losses.
LIVE_WP_MIN = 20.0
LIVE_WP_MAX = 90.0


def win_prob(cp_mover: float) -> float:
    """Win probability for the side to move (0..100 %), from its cp advantage."""
    return 100.0 / (1.0 + math.exp(-WP_SCALE * cp_mover))


def move_wp_loss(move: dict) -> float | None:
    """Lost win-probability % in a live position, or ``None`` if the move is skipped.

    eval_before / eval_after are White-relative (see engine.py/classifier.py),
    so for Black we invert the sign. Moves without evals and moves outside the
    calibrated window ``LIVE_WP_MIN < WP_before < LIVE_WP_MAX`` are skipped.
    We read RAW evals (not clamped); no cap is needed.
    """
    eb = move.get("eval_before")
    ea = move.get("eval_after")
    if eb is None or ea is None:
        return None
    sign = 1 if move.get("color") == "White" else -1
    wp_before = win_prob(sign * eb)
    if not LIVE_WP_MIN < wp_before < LIVE_WP_MAX:
        return None
    return max(0.0, wp_before - win_prob(sign * ea))


def compute_player_wp_loss(games: list[Game], player_name: str) -> dict:
    """WP counterpart of compute_player_acpl: overall / by_color / by_phase.

    - overall and by_color — mean of per-game mean wp_loss (per-game =
      mean(wp_loss) over the player's moves), same as compute_player_acpl.
    - by_phase — flat mean of wp_loss over all moves in that phase.
    - Empty slices → None (not 0).
    Moves without eval_before/eval_after and moves in decided positions
    (move_wp_loss -> None) are skipped.
    A game with no eligible moves is excluded from per-game averages.
    """
    if not games:
        return _empty_result(player_name)

    per_game_wp_losses: list[float] = []
    per_game_white_wp_losses: list[float] = []
    per_game_black_wp_losses: list[float] = []
    phase_losses: dict[str, list[float]] = {phase: [] for phase in _PHASES}
    total_moves_analyzed = 0
    games_count = 0

    for game in games:
        color = resolve_player_color(game, player_name)
        moves = list(iter_player_moves(game, color))

        game_wp_losses: list[float] = []
        for move in moves:
            wp = move_wp_loss(move)
            if wp is None:
                continue
            game_wp_losses.append(wp)
            phase = move.get("phase")
            if phase in phase_losses:
                phase_losses[phase].append(wp)

        # A game with no eligible moves (all missing eval) does not contribute
        # a per-game point, so it does not pollute the average with a ghost.
        if not game_wp_losses:
            continue

        game_wp = sum(game_wp_losses) / len(game_wp_losses)
        per_game_wp_losses.append(game_wp)
        if color == "White":
            per_game_white_wp_losses.append(game_wp)
        else:
            per_game_black_wp_losses.append(game_wp)

        total_moves_analyzed += len(game_wp_losses)
        games_count += 1

    if games_count == 0:
        return _empty_result(player_name)

    return {
        "player": player_name,
        "games_count": games_count,
        "total_moves_analyzed": total_moves_analyzed,
        "wp_loss": _mean_rounded(per_game_wp_losses),
        "wp_loss_by_color": {
            "white": _mean_rounded(per_game_white_wp_losses),
            "black": _mean_rounded(per_game_black_wp_losses),
        },
        "wp_loss_by_phase": {phase: _mean_rounded(phase_losses[phase]) for phase in _PHASES},
    }


def _mean_rounded(values: list[float], ndigits: int = 2) -> float | None:
    """Average a list to 2 decimals, or ``None`` for empty input.

    ``None`` rather than ``0`` because a missing slice (no black games, no
    endgame moves) is qualitatively different from "averaged to zero". WP is
    finer-grained than ACPL, so we keep 2 decimals by default.
    """
    if not values:
        return None
    return round(sum(values) / len(values), ndigits)


def _empty_result(player_name: str) -> dict:
    """Zero-game response shape, kept in sync with the populated branch."""
    return {
        "player": player_name,
        "games_count": 0,
        "total_moves_analyzed": 0,
        "wp_loss": None,
        "wp_loss_by_color": {"white": None, "black": None},
        "wp_loss_by_phase": {phase: None for phase in _PHASES},
    }
