from __future__ import annotations

from collections import Counter

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import Game
from app.services.aggregation.helpers import (
    get_player_analyzed_games,
    iter_player_moves,
    resolve_player_color,
)

_ERROR_CLASSIFICATIONS = frozenset({"inaccuracy", "mistake", "blunder"})

# Single-letter piece codes carried on each move (ARCHITECTURE.md §3.1) →
# human-readable names. Done here in Python rather than SQL: the per-move data
# lives inside the JSONB ``analysis_data`` blob, and JSONB array aggregation in
# PostgreSQL is awkward and hard to read for this shape (see module note).
_PIECE_NAMES = {
    "P": "Pawn",
    "N": "Knight",
    "B": "Bishop",
    "R": "Rook",
    "Q": "Queen",
    "K": "King",
}


async def get_error_patterns(
    db: AsyncSession,
    player_name: str,
) -> dict:
    """Fetch the player's analyzed games and compute their error patterns."""
    games = await get_player_analyzed_games(db, player_name)
    return compute_error_patterns(games, player_name)


def compute_error_patterns(games: list[Game], player_name: str) -> dict:
    """Frequency analysis of a player's mistakes, sliced by piece and move number.

    Considers only moves classified as ``"inaccuracy"``, ``"mistake"`` or
    ``"blunder"`` (good/excellent/best moves carry no error signal). Two
    independent cuts are returned:

    * ``errors_by_piece`` — every piece the player erred with, ordered by raw
      error count, each annotated with its share of the player's total errors.
    * ``errors_by_move_number`` — the ten move numbers where errors cluster most.

    All aggregation runs in Python on the streamed move dicts: the per-move
    payload lives inside the ``analysis_data`` JSONB column, and array-level
    JSONB aggregation in PostgreSQL is both fiddly and unreadable for this
    shape. We can push it down to SQL later if profiling ever demands it.

    When the player has no recorded errors at all, both lists come back empty.
    """
    piece_counts: Counter[str] = Counter()
    move_num_counts: Counter[int] = Counter()

    for game in games:
        color = resolve_player_color(game, player_name)
        for move in iter_player_moves(game, color):
            if move.get("classification") not in _ERROR_CLASSIFICATIONS:
                continue
            piece_counts[move["piece"]] += 1
            move_num_counts[move["move_num"]] += 1

    total_errors = sum(piece_counts.values())
    if total_errors == 0:
        return {"errors_by_piece": [], "errors_by_move_number": []}

    errors_by_piece = [
        {
            "piece": piece,
            "piece_name": _PIECE_NAMES.get(piece, piece),
            "error_count": count,
            "error_pct": round(count / total_errors * 100, 1),
        }
        for piece, count in sorted(
            piece_counts.items(), key=lambda item: item[1], reverse=True
        )
    ]

    errors_by_move_number = [
        {"move_num": move_num, "error_count": count}
        for move_num, count in sorted(
            move_num_counts.items(), key=lambda item: item[1], reverse=True
        )[:10]
    ]

    return {
        "errors_by_piece": errors_by_piece,
        "errors_by_move_number": errors_by_move_number,
    }
