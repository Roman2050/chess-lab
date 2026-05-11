"""Unit tests for EcoLookup (minimal temp JSON, no data/eco.json)."""

import json

import chess
import pytest

from app.services.eco import EcoLookup


def _board_after_sans(sans: list[str]) -> chess.Board:
    b = chess.Board()
    for san in sans:
        b.push_san(san)
    return b


@pytest.fixture
def eco_path_sicilian(tmp_path) -> str:
    b = _board_after_sans(["e4", "c5"])
    path = tmp_path / "eco.json"
    rows = [
        {
            "eco": "B20",
            "name": "Sicilian Defence",
            "fen": b.fen(),
            "moves": "e4 c5",
        },
    ]
    path.write_text(json.dumps(rows), encoding="utf-8")
    return str(path)


@pytest.fixture
def eco_path_indian_by_moves(tmp_path) -> str:
    """FEN in row does not match live board so lookup falls back to SAN sequence."""
    path = tmp_path / "eco.json"
    rows = [
        {
            "eco": "E00",
            "name": "Indian Defence (test line)",
            "fen": chess.Board().fen(),
            "moves": "d4 Nf6",
        },
    ]
    path.write_text(json.dumps(rows), encoding="utf-8")
    return str(path)


@pytest.mark.unit
def test_lookup_by_fen(eco_path_sicilian: str) -> None:
    lookup = EcoLookup(eco_path_sicilian)
    board = _board_after_sans(["e4", "c5"])
    hit = lookup.lookup(board)
    assert hit is not None
    assert hit["name"] == "Sicilian Defence"


@pytest.mark.unit
def test_lookup_by_moves(eco_path_indian_by_moves: str) -> None:
    lookup = EcoLookup(eco_path_indian_by_moves)
    board = _board_after_sans(["d4", "Nf6"])
    hit = lookup.lookup(board)
    assert hit is not None
    assert "Indian" in hit["name"]


@pytest.mark.unit
def test_lookup_unknown(tmp_path) -> None:
    path = tmp_path / "eco.json"
    path.write_text("[]", encoding="utf-8")
    lookup = EcoLookup(str(path))

    assert lookup.lookup(chess.Board()) is None

    weird = chess.Board()
    weird.clear_board()
    weird.set_piece_at(chess.A1, chess.Piece(chess.ROOK, chess.WHITE))
    assert lookup.lookup(weird) is None


@pytest.mark.unit
def test_lookup_returns_eco_code(eco_path_sicilian: str) -> None:
    lookup = EcoLookup(eco_path_sicilian)
    board = _board_after_sans(["e4", "c5"])
    hit = lookup.lookup(board)
    assert hit is not None
    assert set(hit.keys()) == {"eco", "name"}
    assert hit["eco"] == "B20"
    assert hit["name"] == "Sicilian Defence"
