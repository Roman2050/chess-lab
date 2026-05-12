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
    if best_move is None:
        return None

    piece_values = {
        chess.PAWN: 1,
        chess.KNIGHT: 3,
        chess.BISHOP: 3,
        chess.ROOK: 5,
        chess.QUEEN: 9,
        chess.KING: 100,
    }

    test_board = board_before.copy()
    test_board.push(best_move)

    # After pushing `best_move`, it's the opponent's turn on `test_board`,
    # so the piece that just moved (our attacker) is the opposite color.
    opponent_color = test_board.turn

    attacker_square = best_move.to_square
    attacker_piece = test_board.piece_at(attacker_square)
    if attacker_piece is None:
        return None

    attacker_value = piece_values.get(attacker_piece.piece_type)
    if attacker_value is None:
        return None

    targets = 0
    for sq in test_board.attacks(attacker_square):
        target = test_board.piece_at(sq)
        if target is None or target.color != opponent_color:
            continue

        target_value = piece_values.get(target.piece_type)
        if target_value is None:
            continue

        is_defended = test_board.is_attacked_by(opponent_color, sq)
        if not is_defended or target_value < attacker_value:
            targets += 1

    if targets >= 2:
        return "missed_fork"
    return None


def _detect_missed_pin(
    board_before: chess.Board,
    board_after: chess.Board,
    move: chess.Move,
    best_move: chess.Move | None,
) -> str | None:
    """`missed_pin`: `best_move` could have pinned an enemy piece to its king/queen."""
    if best_move is None:
        return None

    piece_values = {
        chess.PAWN: 1,
        chess.KNIGHT: 3,
        chess.BISHOP: 3,
        chess.ROOK: 5,
        chess.QUEEN: 9,
        chess.KING: 100,
    }

    test_board = board_before.copy()
    test_board.push(best_move)

    # After pushing `best_move`, it's the opponent's turn on `test_board`,
    # so our slider (the would-be pinner) is the opposite color.
    opponent_color = test_board.turn

    attacker_square = best_move.to_square
    attacker_piece = test_board.piece_at(attacker_square)
    if attacker_piece is None:
        return None

    # Only sliding pieces (bishop, rook, queen) can deliver a pin.
    if attacker_piece.piece_type not in (chess.BISHOP, chess.ROOK, chess.QUEEN):
        return None

    attacker_value = piece_values[attacker_piece.piece_type]

    for target_sq in test_board.attacks(attacker_square):
        target = test_board.piece_at(target_sq)
        if target is None or target.color != opponent_color:
            continue

        target_value = piece_values.get(target.piece_type)
        if target_value is None:
            continue

        # Absolute pin to the enemy king — let python-chess do the ray geometry.
        if not test_board.is_pinned(opponent_color, target_sq):
            continue

        # Make sure our slider is the one delivering the pin, not some other
        # piece already on the same line.
        if attacker_square not in test_board.pin(opponent_color, target_sq):
            continue

        # A pin only matters tactically when the pinned piece is worth more
        # than the pinner; otherwise the defender just trades it off.
        if target_value > attacker_value:
            return "missed_pin"

    return None


def _detect_hanging_piece(
    board_before: chess.Board,
    board_after: chess.Board,
    move: chess.Move,
    best_move: chess.Move | None,
) -> str | None:
    """`hanging_piece`: the played move left one of our own pieces attacked and undefended."""
    piece_values = {
        chess.PAWN: 1,
        chess.KNIGHT: 3,
        chess.BISHOP: 3,
        chess.ROOK: 5,
        chess.QUEEN: 9,
    }

    # After `move` was played, it's the opponent's turn on `board_after`,
    # so the side that just moved (our side) is the opposite color.
    own_color = not board_after.turn
    opponent_color = board_after.turn

    for square in chess.SQUARES:
        piece = board_after.piece_at(square)
        if piece is None or piece.color != own_color:
            continue

        piece_value = piece_values.get(piece.piece_type)
        if piece_value is None:
            continue

        if not board_after.is_attacked_by(opponent_color, square):
            continue

        if board_after.is_attacked_by(own_color, square):
            continue

        # Skip "equal trades": only flag when the cheapest attacker is worth
        # strictly less than the hanging piece.
        attacker_values = [
            piece_values[board_after.piece_at(att_sq).piece_type]
            for att_sq in board_after.attackers(opponent_color, square)
            if board_after.piece_at(att_sq).piece_type in piece_values
        ]
        if not attacker_values:
            continue

        if piece_value > min(attacker_values):
            return "hanging_piece"

    return None


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
