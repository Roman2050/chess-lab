from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import Game
from app.services.aggregation.helpers import (
    get_player_analyzed_games,
    iter_player_moves,
    resolve_player_color,
)

_PHASES: tuple[str, ...] = ("opening", "middlegame", "endgame")


async def get_player_acpl(
    db: AsyncSession,
    player_name: str,
    time_control: str | None = None,
) -> dict:
    """Fetch the player's analyzed games and compute their ACPL breakdown."""
    games = await get_player_analyzed_games(db, player_name, time_control)
    return compute_player_acpl(games, player_name)


def compute_player_acpl(games: list[Game], player_name: str) -> dict:
    """Aggregate a player's ACPL with color and phase breakdowns.

    The overall ACPL is the **mean of per-game ACPLs** (each game first
    collapsed to ``sum(cp_loss) / N_player_moves``), not a flat mean over every
    move. This matches how chess sites report it and avoids letting a single
    long, sloppy game swamp the average just because it has more moves.

    `acpl_by_color` follows the same per-game-then-average rule, partitioned by
    the side the player held in each game.

    `acpl_by_phase` deliberately uses a **flat mean of cp_loss over every move
    in that phase** instead — most games never enter the endgame, and using a
    per-game average would force us to either drop those games (biasing the
    sample) or count them as zero (lying). A flat per-move mean handles the
    "some games have zero endgame moves" reality cleanly.

    Empty slices (e.g. the player never had the white pieces, or no game
    reached the endgame) return ``None`` rather than ``0`` so the caller can
    tell "no data" apart from "averaged out to zero".
    """
    if not games:
        return _empty_result(player_name)

    per_game_acpls: list[float] = []
    per_game_white_acpls: list[float] = []
    per_game_black_acpls: list[float] = []
    phase_losses: dict[str, list[int]] = {phase: [] for phase in _PHASES}
    total_moves_analyzed = 0
    games_count = 0

    for game in games:
        color = resolve_player_color(game, player_name)
        moves = list(iter_player_moves(game, color))

        # Defensive: an analyzed game with zero player moves would be an
        # upstream anomaly (mismatched color, truncated JSON). Skip it so we
        # don't divide by zero and don't pollute the per-game average with a
        # ghost data point.
        if not moves:
            continue

        game_acpl = sum(move["cp_loss"] for move in moves) / len(moves)

        per_game_acpls.append(game_acpl)
        if color == "White":
            per_game_white_acpls.append(game_acpl)
        else:
            per_game_black_acpls.append(game_acpl)

        for move in moves:
            phase = move.get("phase")
            if phase in phase_losses:
                phase_losses[phase].append(move["cp_loss"])

        total_moves_analyzed += len(moves)
        games_count += 1

    if games_count == 0:
        return _empty_result(player_name)

    return {
        "player": player_name,
        "games_count": games_count,
        "total_moves_analyzed": total_moves_analyzed,
        "acpl": _mean_rounded(per_game_acpls),
        "acpl_by_color": {
            "white": _mean_rounded(per_game_white_acpls),
            "black": _mean_rounded(per_game_black_acpls),
        },
        "acpl_by_phase": {phase: _mean_rounded(phase_losses[phase]) for phase in _PHASES},
    }


def _mean_rounded(values: list[float] | list[int]) -> float | None:
    """Average a list to 1 decimal, or ``None`` for empty input.

    ``None`` rather than ``0`` because a missing slice (no black games, no
    endgame moves) is qualitatively different from "averaged to zero", and the
    response schema needs to surface that distinction.
    """
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def _empty_result(player_name: str) -> dict:
    """Zero-game response shape, kept in sync with the populated branch."""
    return {
        "player": player_name,
        "games_count": 0,
        "total_moves_analyzed": 0,
        "acpl": None,
        "acpl_by_color": {"white": None, "black": None},
        "acpl_by_phase": {phase: None for phase in _PHASES},
    }
