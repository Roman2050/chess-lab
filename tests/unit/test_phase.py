import chess
import pytest

from app.services.analysis.phase import (
    ENDGAME_MATERIAL_THRESHOLD,
    detect_phase,
)


@pytest.mark.unit
def test_starting_position_is_opening() -> None:
    """Move 1 from the initial position: queens on board, full material, zero
    developed minors, ply well under the cutoff → opening.
    """
    board = chess.Board()

    assert detect_phase(board, ply=1) == "opening"


@pytest.mark.unit
def test_developed_middlegame() -> None:
    """1.e4 e5 2.Nf3 Nc6 3.Bc4 Bc5 4.O-O Nf6 5.d3 d6 — by move 5 both sides
    have pushed 5 minors off the back rank, so the development guard fires
    even though we're still inside ``OPENING_MAX_PLY``. Verifies that
    advanced development alone is enough to leave the opening.
    """
    board = chess.Board()
    for san in (
        "e4",
        "e5",
        "Nf3",
        "Nc6",
        "Bc4",
        "Bc5",
        "O-O",
        "Nf6",
        "d3",
        "d6",
    ):
        board.push_san(san)

    assert detect_phase(board, ply=10) == "middlegame"


@pytest.mark.unit
def test_late_opening_undeveloped() -> None:
    """Pawn-only flank shuffle: 14 plies played, no minor has moved. At
    ply=15 we're still inside the ply cutoff with development well below
    the threshold → opening, despite the high move count.
    """
    board = chess.Board()
    for san in (
        "a3",
        "a6",
        "b3",
        "b6",
        "c3",
        "c6",
        "d3",
        "d6",
        "e3",
        "e6",
        "f3",
        "f6",
        "g3",
        "g6",
    ):
        board.push_san(san)

    assert detect_phase(board, ply=15) == "opening"


@pytest.mark.unit
def test_endgame_no_queens() -> None:
    """Starting position minus both queens: queens-on-board count is zero, so
    the endgame rule fires regardless of how much other material is left
    (4 rooks + 4 minors here ⇒ 16 material points, well above the threshold).
    """
    board = chess.Board()
    board.remove_piece_at(chess.D1)
    board.remove_piece_at(chess.D8)

    assert detect_phase(board, ply=30) == "endgame"


@pytest.mark.unit
def test_endgame_low_material() -> None:
    """K + 2P vs K + 2P: no non-pawn material at all, so both endgame triggers
    (queens=0 and material<=threshold) fire simultaneously.
    """
    board = chess.Board("4k3/3p1p2/8/8/8/8/3P1P2/4K3 w - - 0 80")

    assert detect_phase(board, ply=80) == "endgame"


@pytest.mark.unit
def test_middlegame_with_queens_full_material() -> None:
    """All minors developed, queens still on the board, ply past the opening
    cutoff. Endgame rule doesn't fire (queens present, material=24); opening
    rule doesn't fire (ply > 20 AND development > threshold) → middlegame.
    """
    board = chess.Board("r2qk2r/ppp1bppp/2nb1n2/3p4/3P4/2NB1N2/PPP1BPPP/R2QK2R w KQkq - 0 13")

    assert detect_phase(board, ply=25) == "middlegame"


@pytest.mark.unit
def test_boundary_material() -> None:
    """K+Q+R vs K+Q ⇒ material = 4 (Q) + 2 (R) + 4 (Q) = 10, exactly the
    threshold. Queens are still on the board, so this only exercises the
    material branch; ``<=`` makes the boundary inclusive → endgame.
    """
    board = chess.Board("3qk3/8/8/8/8/8/8/3QK2R w K - 0 1")

    assert detect_phase(board, ply=40) == "endgame"


@pytest.mark.unit
def test_boundary_material_constant_is_ten() -> None:
    """Guardrail: the suite above assumes the threshold is 10. If somebody
    retunes the constant, this test trips so the boundary case gets revisited.
    """
    assert ENDGAME_MATERIAL_THRESHOLD == 10
