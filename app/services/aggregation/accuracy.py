from __future__ import annotations

from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import Game
from app.services.aggregation.helpers import (
    get_player_analyzed_games,
    iter_player_moves,
    resolve_player_color,
)
from app.services.aggregation.winprob import move_wp_loss

_PHASES: tuple[str, ...] = ("opening", "middlegame", "endgame")


async def get_accuracy_by_phase(
    db: AsyncSession,
    player_name: str,
) -> dict:
    """Fetch the player's analyzed games and compute per-phase accuracy."""
    games = await get_player_analyzed_games(db, player_name)
    return compute_accuracy_by_phase(games, player_name)


def compute_accuracy_by_phase(games: list[Game], player_name: str) -> dict:
    """Aggregate accuracy metrics for a player, split by game phase.

    For each phase (opening / middlegame / endgame) we compute a flat
    per-move ACPL plus the share of moves classified as ``inaccuracy``,
    ``mistake`` and ``blunder``. Rates are kept as three separate fields
    instead of a fused ``error_rate``: the frontend can always recombine
    them into a composite, but once you average three signals into one
    you can't recover the detail. Surfacing them individually also lets
    the report flag e.g. "lots of inaccuracies but few blunders" — a
    qualitatively different profile from the inverse.

    A phase with zero recorded moves returns a dict where every metric is
    ``None`` and ``moves_count`` is ``0``. The shape stays stable so the
    frontend never has to defend against a missing key.
    """
    phase_cp_losses: dict[str, list[int]] = {phase: [] for phase in _PHASES}
    phase_classifications: dict[str, list[str]] = {phase: [] for phase in _PHASES}

    for game in games:
        color = resolve_player_color(game, player_name)
        for move in iter_player_moves(game, color):
            phase = move.get("phase")
            if phase not in phase_cp_losses:
                continue
            phase_cp_losses[phase].append(move["cp_loss"])
            phase_classifications[phase].append(move.get("classification", ""))

    return {
        phase: _phase_stats(phase_cp_losses[phase], phase_classifications[phase])
        for phase in _PHASES
    }


async def get_accuracy_by_move_number(
    db: AsyncSession,
    player_name: str,
    min_games: int = 5,
) -> list[dict]:
    """Aggregate accuracy metrics for a player, split by move number.

    Each row carries both ``avg_cp_loss`` (raw centipawn loss) and
    ``avg_wp_loss`` (win-probability loss, §3.6): the latter is a flat
    per-move mean of :func:`move_wp_loss` over moves at that move number that
    carry engine evals, or ``None`` when none do (keeps the row shape stable).

    Returns one row per ``move_num``, sorted ascending. ``games_count`` is
    the number of **distinct games** the player reached that move in —
    in practice this equals the move list length (a player has at most
    one move per move number per game), but we count distinct game ids
    explicitly so the contract is obvious from the code and any upstream
    duplication shows up as a bug instead of silently inflating the
    denominator.

    Move numbers with ``games_count < min_games`` are dropped — late
    middlegame and endgame moves naturally appear in only a handful of
    games, and tiny denominators produce wildly noisy rates. ``min_games``
    is a parameter (default 5) so callers can tighten or relax the cutoff
    per context (e.g. a noisy opening preview vs. a strict report).

    Deliberately **no** ``phase`` field on each row: the same move number
    can land in different phases across different games (move 25 is still
    middlegame in one game, already endgame in another), so any single
    phase label here would be either an arbitrary majority vote or a flat
    lie. Callers that want phase context should use
    :func:`get_accuracy_by_phase` instead.
    """
    games = await get_player_analyzed_games(db, player_name)

    per_move_cp_losses: dict[int, list[int]] = defaultdict(list)
    per_move_wp_losses: dict[int, list[float]] = defaultdict(list)
    per_move_classifications: dict[int, list[str]] = defaultdict(list)
    per_move_game_ids: dict[int, set[int]] = defaultdict(set)

    for game in games:
        color = resolve_player_color(game, player_name)
        for move in iter_player_moves(game, color):
            move_num = move.get("move_num")
            if move_num is None:
                continue
            per_move_cp_losses[move_num].append(move["cp_loss"])
            wp = move_wp_loss(move)
            if wp is not None:
                per_move_wp_losses[move_num].append(wp)
            per_move_classifications[move_num].append(move.get("classification", ""))
            per_move_game_ids[move_num].add(game.id)

    rows: list[dict] = []
    for move_num in sorted(per_move_cp_losses):
        games_count = len(per_move_game_ids[move_num])
        if games_count < min_games:
            continue

        cp_losses = per_move_cp_losses[move_num]
        wp_losses = per_move_wp_losses[move_num]
        classifications = per_move_classifications[move_num]
        n = len(cp_losses)

        row = {
            "move_num": move_num,
            "games_count": games_count,
            "avg_cp_loss": round(sum(cp_losses) / n, 1),
            "avg_wp_loss": (
                round(sum(wp_losses) / len(wp_losses), 2) if wp_losses else None
            ),
        }
        row.update(_error_rates(classifications, n))
        rows.append(row)

    return rows


def _phase_stats(cp_losses: list[int], classifications: list[str]) -> dict:
    """Build the per-phase stats dict, keeping shape stable when empty.

    Empty phases get ``None`` for every metric (vs. ``0``) so callers can
    tell "this player never reached the endgame" apart from "this player
    reached the endgame and played it perfectly". ``moves_count`` always
    stays an integer — it's the one field where ``0`` is meaningful.
    """
    n = len(cp_losses)
    if n == 0:
        return {
            "acpl": None,
            "inaccuracy_rate": None,
            "mistake_rate": None,
            "blunder_rate": None,
            "moves_count": 0,
        }
    return {
        "acpl": round(sum(cp_losses) / n, 1),
        **_error_rates(classifications, n),
        "moves_count": n,
    }


def _error_rates(classifications: list[str], n: int) -> dict[str, float]:
    """Compute inaccuracy/mistake/blunder rates as percentages (1 d.p.).

    Caller guarantees ``n > 0`` — empty buckets are handled one level up
    by returning ``None`` for every rate, which this helper deliberately
    cannot express.
    """
    return {
        "inaccuracy_rate": round(classifications.count("inaccuracy") / n * 100, 1),
        "mistake_rate": round(classifications.count("mistake") / n * 100, 1),
        "blunder_rate": round(classifications.count("blunder") / n * 100, 1),
    }
