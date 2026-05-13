import chess
import pytest

from app.services.analysis.tactical import (
    _detect_hanging_piece,
    _detect_missed_fork,
    detect_tactical_tags,
)


def _empty_board_with_kings() -> chess.Board:
    """An otherwise-empty board with kings parked in safe corners.

    The detectors only need legal-ish positions: kings present, side-to-move
    set explicitly per test. We park them on e1/h8 (far apart, never adjacent
    to the squares we care about) so they never accidentally defend a piece.
    """
    board = chess.Board.empty()
    board.set_piece_at(chess.E1, chess.Piece(chess.KING, chess.WHITE))
    board.set_piece_at(chess.H8, chess.Piece(chess.KING, chess.BLACK))
    return board


@pytest.mark.unit
def test_hanging_piece_detected() -> None:
    """White queen attacked by an undefended pawn → `hanging_piece`.

    `board_after.turn == BLACK` means White just moved, so `own_color = WHITE`
    in the detector. The queen on d4 is attacked by the c5 pawn and has no
    defender; attacker value (pawn=1) < queen value (9) → fires.
    """
    board_after = _empty_board_with_kings()
    board_after.set_piece_at(chess.D4, chess.Piece(chess.QUEEN, chess.WHITE))
    board_after.set_piece_at(chess.C5, chess.Piece(chess.PAWN, chess.BLACK))
    board_after.turn = chess.BLACK

    result = _detect_hanging_piece(
        board_before=board_after,  # unused by this detector
        board_after=board_after,
        move=chess.Move.null(),
        best_move=None,
    )

    assert result == "hanging_piece"


@pytest.mark.unit
def test_hanging_piece_not_triggered_on_equal_trade() -> None:
    """Knight-for-knight is not "hanging" — the detector skips equal trades.

    White knight on c3 is attacked by Black knight on b5 with no defender,
    but attacker value (3) is not strictly less than piece value (3), so the
    detector must return `None`.
    """
    board_after = _empty_board_with_kings()
    board_after.set_piece_at(chess.C3, chess.Piece(chess.KNIGHT, chess.WHITE))
    board_after.set_piece_at(chess.B5, chess.Piece(chess.KNIGHT, chess.BLACK))
    board_after.turn = chess.BLACK

    result = _detect_hanging_piece(
        board_before=board_after,
        board_after=board_after,
        move=chess.Move.null(),
        best_move=None,
    )

    assert result is None


@pytest.mark.unit
def test_missed_fork_detected() -> None:
    """Best move `c4→d6` forks an undefended knight and rook → `missed_fork`.

    From d6 a white knight attacks b7 (Black N) and c8 (Black R). Both are
    undefended in this position, so the detector counts ≥2 targets and fires.
    The actually-played move is irrelevant for `_detect_missed_fork` — only
    `board_before` and `best_move` matter.
    """
    board_before = _empty_board_with_kings()
    board_before.set_piece_at(chess.C4, chess.Piece(chess.KNIGHT, chess.WHITE))
    board_before.set_piece_at(chess.B7, chess.Piece(chess.KNIGHT, chess.BLACK))
    board_before.set_piece_at(chess.C8, chess.Piece(chess.ROOK, chess.BLACK))
    board_before.turn = chess.WHITE

    best_move = chess.Move(chess.C4, chess.D6)
    played_move = chess.Move(chess.C4, chess.B6)  # some weaker alternative

    result = _detect_missed_fork(
        board_before=board_before,
        board_after=board_before.copy(stack=False),  # unused by this detector
        move=played_move,
        best_move=best_move,
    )

    assert result == "missed_fork"


@pytest.mark.unit
def test_no_tags_on_good_move() -> None:
    """A normal opening move on the starting board produces no tactical tags.

    `1.e4` from the starting position is quiet: no piece is hanging, no fork
    or pin is available, and there's no pre-existing threat to ignore. The
    classifier wouldn't call the detector here anyway (only error-class moves
    trigger it), but the detector must still return `[]` when invoked directly.
    """
    board_before = chess.Board()
    move = board_before.parse_san("e4")
    board_after = board_before.copy(stack=False)
    board_after.push(move)

    tags = detect_tactical_tags(
        board_before=board_before,
        board_after=board_after,
        move=move,
        best_move=move,
    )

    assert tags == []
