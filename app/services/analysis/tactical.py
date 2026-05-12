from __future__ import annotations

import chess

# Phase 3 heuristic tactical tag detector (see ARCHITECTURE.md §8).
# Each `_detect_*` function inspects the position around a single move and
# returns either a tag string (when the heuristic fires) or `None`. The public
# `detect_tactical_tags` aggregates them into the `tactical_tags` list attached
# to error moves in `analysis_data` (see ARCHITECTURE.md §5.3).


def detect_tactical_tags(
    board_before: chess.Board,
    board_after: chess.Board,
    move: chess.Move,
    best_move: chess.Move | None,
) -> list[str]:
    """Run every heuristic detector and collect the tags that fired.

    `board_before` / `board_after` are the positions immediately before and
    after `move` was played. `best_move` is the engine's top recommendation
    in `board_before` (may be `None` if MultiPV data is unavailable).

    Returns a possibly-empty list of tag strings. Tags are stable identifiers
    consumed downstream by the LLM report builder.
    """
    detectors = (
        _detect_missed_fork(board_before, board_after, move, best_move),
        _detect_missed_pin(board_before, board_after, move, best_move),
        _detect_hanging_piece(board_before, board_after, move, best_move),
        _detect_missed_threat(board_before, board_after, move, best_move),
    )
    return [tag for tag in detectors if tag is not None]


def _detect_missed_fork(
    board_before: chess.Board,
    board_after: chess.Board,
    move: chess.Move,
    best_move: chess.Move | None,
) -> str | None:
    """`missed_fork`: `best_move` would have attacked 2+ undefended enemy pieces."""
    # TODO: Phase 3 — replay `best_move` on a copy of `board_before`, enumerate
    # squares attacked by the moved piece, count enemy pieces on those squares
    # that have no defenders. Fire when count >= 2.
    pass


def _detect_missed_pin(
    board_before: chess.Board,
    board_after: chess.Board,
    move: chess.Move,
    best_move: chess.Move | None,
) -> str | None:
    """`missed_pin`: `best_move` could have pinned an enemy piece to its king/queen."""
    # TODO: Phase 3 — after replaying `best_move`, scan rays from the moved
    # sliding piece for an enemy piece with the enemy king or queen directly
    # behind it on the same ray.
    pass


def _detect_hanging_piece(
    board_before: chess.Board,
    board_after: chess.Board,
    move: chess.Move,
    best_move: chess.Move | None,
) -> str | None:
    """`hanging_piece`: the played move left one of our own pieces attacked and undefended."""
    # TODO: Phase 3 — on `board_after`, find friendly pieces (of the side that
    # just moved) that are attacked by the opponent and have no defenders, or
    # where the cheapest attacker is worth less than the piece.
    pass


def _detect_missed_threat(
    board_before: chess.Board,
    board_after: chess.Board,
    move: chess.Move,
    best_move: chess.Move | None,
) -> str | None:
    """`missed_threat`: opponent had a clear threat in `board_before` that `move` did not parry."""
    # TODO: Phase 3 — detect threats present in `board_before` (e.g. mate-in-1,
    # winning capture for the opponent on their next move) and check that they
    # remain available in `board_after`.
    pass
