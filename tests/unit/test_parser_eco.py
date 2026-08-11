"""Parser opening resolution: PGN Opening tag vs mocked ECO lookup."""

from unittest.mock import MagicMock, patch

import pytest

from app.utils.parser import parse_pgn_text


def _minimal_pgn(*, opening_tag: str | None = None, moves: str = "1. e4 e5 1-0\n") -> str:
    lines = [
        '[Event "Unit"]',
        '[Site "https://lichess.org/unitparser1"]',
        '[Date "2026.05.06"]',
        '[Result "1-0"]',
        '[Variant "Standard"]',
    ]
    if opening_tag is not None:
        lines.append(f'[Opening "{opening_tag}"]')
    return "\n".join(lines) + "\n\n" + moves


@pytest.mark.unit
@patch("app.utils.parser.get_eco_lookup")
def test_opening_from_pgn_tag(mock_get_eco: MagicMock) -> None:
    eco = MagicMock()
    mock_get_eco.return_value = eco

    pgn = _minimal_pgn(opening_tag="Custom From Tag", moves="1. e4 e5 1-0\n")
    result = parse_pgn_text(pgn)

    assert len(result) == 1
    assert result[0]["opening_name"] == "Custom From Tag"
    eco.lookup.assert_not_called()


@pytest.mark.unit
@patch("app.utils.parser.get_eco_lookup")
def test_opening_from_eco_lookup(mock_get_eco: MagicMock) -> None:
    eco = MagicMock()
    eco.lookup.return_value = {"eco": "C70", "name": "Spanish Game"}
    mock_get_eco.return_value = eco

    pgn = _minimal_pgn(opening_tag=None, moves="1. e4 e5 2. Nf3 Nc6 3. Bb5 1-0\n")
    result = parse_pgn_text(pgn)

    assert len(result) == 1
    assert result[0]["opening_name"] == "Spanish Game"
    assert eco.lookup.called


@pytest.mark.unit
@patch("app.utils.parser.get_eco_lookup")
def test_opening_fallback_unknown(mock_get_eco: MagicMock) -> None:
    eco = MagicMock()
    eco.lookup.return_value = None
    mock_get_eco.return_value = eco

    pgn = _minimal_pgn(opening_tag=None, moves="1. e4 e5 1-0\n")
    result = parse_pgn_text(pgn)

    assert len(result) == 1
    assert result[0]["opening_name"] == "Unknown"
