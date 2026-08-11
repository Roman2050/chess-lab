from __future__ import annotations

import chess

# Endgame trigger: total non-pawn material across both sides (kings excluded,
# weights N=B=1, R=2, Q=4 → starting total is 24). Inclusive upper bound, so a
# position sitting exactly on the threshold counts as endgame.
ENDGAME_MATERIAL_THRESHOLD = 10

# Opening cutoff: past this many plies we assume the opening is over even if
# pieces are still on the back rank — a player parking minors past move 10 has
# already left the opening phase by every practical definition.
OPENING_MAX_PLY = 20

# Minimum count of developed minors (knights + bishops off their starting back
# rank, both colors summed) required to leave the opening early. Picked so the
# typical "both sides developed both minors" position (4 pieces) flips out.
OPENING_DEVELOPED_THRESHOLD = 4

_NON_PAWN_WEIGHTS: dict[chess.PieceType, int] = {
    chess.KNIGHT: 1,
    chess.BISHOP: 1,
    chess.ROOK: 2,
    chess.QUEEN: 4,
}

# Starting back rank for each color, expressed as a 0-indexed rank for use with
# `chess.square_rank()` (rank 1 ↔ 0, rank 8 ↔ 7).
_STARTING_RANK: dict[chess.Color, int] = {
    chess.WHITE: 0,
    chess.BLACK: 7,
}


def detect_phase(board: chess.Board, ply: int) -> str:
    """Classify the game phase for the move about to be made on `board`.

    `board` is the position **before** the move at `ply` (1-based). The function
    is pure — no DB, no engine, no side effects — so it's safe to call from any
    layer (aggregation pipelines, tactical heuristics, frontend export, etc.).

    Returns one of ``"opening"``, ``"middlegame"``, ``"endgame"``. Rules, in
    order:

    1. **Endgame** if either queens have left the board entirely, or total
       non-pawn material is at/below ``ENDGAME_MATERIAL_THRESHOLD`` (inclusive).
    2. **Opening** if we're still inside ``OPENING_MAX_PLY`` and fewer than
       ``OPENING_DEVELOPED_THRESHOLD`` minor pieces have left their back rank —
       i.e. development is genuinely incomplete.
    3. **Middlegame** otherwise.
    """
    queens = len(board.pieces(chess.QUEEN, chess.WHITE)) + len(
        board.pieces(chess.QUEEN, chess.BLACK)
    )

    material = 0
    for piece_type, weight in _NON_PAWN_WEIGHTS.items():
        material += weight * (
            len(board.pieces(piece_type, chess.WHITE)) + len(board.pieces(piece_type, chess.BLACK))
        )

    if queens == 0 or material <= ENDGAME_MATERIAL_THRESHOLD:
        return "endgame"

    if ply <= OPENING_MAX_PLY and _count_developed_minors(board) < OPENING_DEVELOPED_THRESHOLD:
        return "opening"

    return "middlegame"


def _count_developed_minors(board: chess.Board) -> int:
    """Count knights and bishops (both colors) that have left their back rank.

    A minor sitting on its starting rank is treated as undeveloped regardless of
    its file — we don't try to distinguish "Bc1 hasn't moved" from "Bf1 retreated
    to c1". That's a known false positive but it's symmetric for both sides and
    keeps the heuristic dead-simple.
    """
    count = 0
    for color in (chess.WHITE, chess.BLACK):
        starting_rank = _STARTING_RANK[color]
        for piece_type in (chess.KNIGHT, chess.BISHOP):
            for square in board.pieces(piece_type, color):
                if chess.square_rank(square) != starting_rank:
                    count += 1
    return count
