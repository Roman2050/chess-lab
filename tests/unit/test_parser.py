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


@pytest.mark.unit
def test_parse_pgn_text_hashes_unique_id_when_site_not_lichess(sample_pgn_text: str) -> None:
    non_lichess_site_pgn = sample_pgn_text.replace(
        '[Site "https://lichess.org/abcdef12"]',
        '[Site "https://example.com/game/whatever"]',
    )

    result = parse_pgn_text(non_lichess_site_pgn)
    assert len(result) == 1

    unique_id = result[0]["unique_id"]
    assert unique_id != "abcdef12"
    assert isinstance(unique_id, str)
    assert len(unique_id) == 64
    assert all(c in "0123456789abcdef" for c in unique_id)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("result_tag", "expected_winner"),
    [
        ("0-1", "Black"),
        ("1/2-1/2", "Draw"),
    ],
)
def test_parse_pgn_text_winner_mapping_for_black_and_draw(
    sample_pgn_text: str,
    result_tag: str,
    expected_winner: str,
) -> None:
    # Keep header and movetext result consistent (python-chess may infer header from movetext).
    pgn = (
        sample_pgn_text.replace('[Result "1-0"]', f'[Result "{result_tag}"]')
        .replace(" 1-0\n", f" {result_tag}\n")
    )

    result = parse_pgn_text(pgn)
    assert len(result) == 1
    assert result[0]["winner"] == expected_winner


@pytest.mark.unit
def test_parse_pgn_text_parses_multiple_games_in_one_text(sample_pgn_text: str) -> None:
    second_game = (
        sample_pgn_text.replace('[Site "https://lichess.org/abcdef12"]', '[Site "https://lichess.org/zzzz9999"]')
        .replace('[White "WhitePlayer"]', '[White "Alice"]')
        .replace('[Black "BlackPlayer"]', '[Black "Bob"]')
        .replace('[Result "1-0"]', '[Result "0-1"]')
        .replace(" 1-0\n", " 0-1\n")
    )

    combined = sample_pgn_text.rstrip() + "\n\n" + second_game.lstrip()

    result = parse_pgn_text(combined)
    assert len(result) == 2

    assert result[0]["unique_id"] == "abcdef12"
    assert result[0]["winner"] == "White"

    assert result[1]["unique_id"] == "zzzz9999"
    assert result[1]["winner"] == "Black"


@pytest.mark.unit
def test_parse_pgn_text_winner_is_none_for_unexpected_result(sample_pgn_text: str) -> None:
    # Intentionally use a non-standard result string and keep it consistent in header + movetext.
    weird_result = "weird"
    pgn = (
        sample_pgn_text.replace('[Result "1-0"]', f'[Result "{weird_result}"]')
        .replace(" 1-0\n", f" {weird_result}\n")
    )

    result = parse_pgn_text(pgn)
    assert len(result) == 1
    assert result[0]["result"] == weird_result
    assert result[0]["winner"] is None


@pytest.mark.unit
def test_parse_pgn_text_defaults_unknown_players_and_none_opening_timecontrol() -> None:
    pgn = (
        '[Event "Test"]\n'
        '[Site "https://lichess.org/nositeid"]\n'
        '[Date "2026.05.06"]\n'
        '[Result "1-0"]\n'
        '[Variant "Standard"]\n'
        "\n"
        "1. e4 e5 1-0\n"
    )

    result = parse_pgn_text(pgn)
    assert len(result) == 1

    game = result[0]
    # python-chess fills missing player tags with "?" in headers
    assert game["white_player"] == "?"
    assert game["black_player"] == "?"
    assert isinstance(game["opening_name"], str)
    assert game["time_control"] is None

