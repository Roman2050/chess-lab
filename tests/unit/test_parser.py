import pytest

from app.utils.parser import parse_pgn_text


@pytest.mark.unit
def test_parse_pgn_text_happy_path_standard_lichess_site(sample_pgn_text: str) -> None:
    result = parse_pgn_text(sample_pgn_text)

    assert len(result) == 1

    game = result[0]
    assert game["unique_id"] == "abcdef12"
    assert game["winner"] == "White"

    pgn_content = game["pgn_content"]
    assert isinstance(pgn_content, str)
    assert pgn_content.strip() != ""

    # headers=False in exporter means tags like [Event "..."] should not be present
    assert "[Event " not in pgn_content
    assert "[Site " not in pgn_content


@pytest.mark.unit
def test_parse_pgn_text_skips_non_standard_variant(sample_pgn_text: str) -> None:
    non_standard_pgn = sample_pgn_text.replace('[Variant "Standard"]', '[Variant "Chess960"]')

    result = parse_pgn_text(non_standard_pgn)

    assert result == []


@pytest.mark.unit
def test_parse_pgn_text_skips_unfinished_games_result_star(sample_pgn_text: str) -> None:
    unfinished_pgn = (
        sample_pgn_text.replace('[Result "1-0"]', '[Result "*"]')
        # PGN result can also be present in the movetext tail; make it consistent.
        .replace(" 1-0\n", " *\n")
    )

    result = parse_pgn_text(unfinished_pgn)

    assert result == []

