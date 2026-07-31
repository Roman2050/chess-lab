from datetime import date, datetime
import hashlib
import io

import chess
import chess.pgn

from app.services.eco import EcoLookup, get_eco_lookup


def _parse_pgn_date(raw_date: str | None) -> date | None:
    """
    Parses PGN Date tag in 'YYYY.MM.DD' format into a datetime.date object.

    Returns None for absent, partial ('????.??.??', '2026.??.??'), or malformed dates
    so that one bad Date tag does not abort parsing of the whole file.
    """
    if not raw_date or "?" in raw_date:
        return None
    try:
        return datetime.strptime(raw_date, "%Y.%m.%d").date()
    except (ValueError, TypeError):
        return None


def _resolve_opening_name(game: chess.pgn.Game, eco_lookup: EcoLookup) -> str:
    """Walk the mainline and return the deepest ECO match, falling back to 'Unknown'."""
    board = chess.Board()
    last_match: str | None = None
    for move in game.mainline_moves():
        board.push(move)
        hit = eco_lookup.lookup(board)
        if hit is not None:
            last_match = hit["name"]
        elif last_match is not None:
            # Left the opening tree — no need to keep scanning the rest of the game
            break
    return last_match or "Unknown"


def parse_pgn_text(pgn_text: str) -> list[dict]:
    """
    Parses PGN text and returns a list of dictionaries containing game data.

    Per ARCHITECTURE.md §3.8:
    - Lichess games use the last path segment of the Site URL as `unique_id`.
    - Custom PGNs use sha256("White|Black|Date|Result|clean_pgn").
    - Date parsing is lenient: absent or malformed dates produce date_played = None.
    """
    games_data = []
    pgn_io = io.StringIO(pgn_text)
    eco_lookup = get_eco_lookup()

    while True:
        game = chess.pgn.read_game(pgn_io)
        if game is None:
            break

        headers = game.headers

        # If the `Variant` tag exists and it is not "Standard," we simply skip this game
        variant = headers.get("Variant", "Standard")
        if variant.lower() != "standard":
            continue

        if headers.get("Result") == "*":
            continue

        # Export the game back to a text-based PGN file for safekeeping
        exporter = chess.pgn.StringExporter(headers=False, variations=False, comments=False)
        clean_pgn = game.accept(exporter)

        # Attempt to retrieve Lichess ID; fallback to sha256("White|Black|Date|Result|clean_pgn")
        site = headers.get("Site", "")
        if "lichess.org/" in site:
            unique_id = site.split("/")[-1]
        else:
            raw_data = "|".join([
                headers.get("White", ""),
                headers.get("Black", ""),
                headers.get("Date", ""),
                headers.get("Result", ""),
                clean_pgn,
            ])
            unique_id = hashlib.sha256(raw_data.encode("utf-8")).hexdigest()

        result = headers.get("Result", "*")
        winner = None

        if result == "1-0":
            winner = "White"
        elif result == "0-1":
            winner = "Black"
        elif result == "1/2-1/2":
            winner = "Draw"

        # Prefer the PGN Opening tag when present; otherwise enrich via local ECO lookup
        opening_tag = headers.get("Opening")
        if opening_tag and opening_tag.strip():
            opening_name = opening_tag
        else:
            opening_name = _resolve_opening_name(game, eco_lookup)

        games_data.append({
            "unique_id": unique_id,
            "white_player": headers.get("White", "Unknown"),
            "black_player": headers.get("Black", "Unknown"),
            "result": result,
            "winner": winner,
            "date_played": _parse_pgn_date(headers.get("Date")),
            "opening_name": opening_name,
            "time_control": headers.get("TimeControl", None),
            "pgn_content": clean_pgn,
        })

    return games_data