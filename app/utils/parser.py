import io
from datetime import datetime

import chess.pgn
import hashlib


def parse_pgn_text(pgn_text: str) -> list[dict]:
    """Parses PGN text and returns a list of databases containing game data."""
    games_data = []
    pgn_io = io.StringIO(pgn_text)

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

        # Attempt to retrieve Lichess ID
        site = headers.get("Site", "")
        if "lichess.org/" in site:
            unique_id = site.split("/")[-1]
        else:
            # If it's a random file, we generate a hash from the moves, names, and date
            raw_data = f"{headers.get('White')}{headers.get('Black')}{headers.get('Date')}{game.board().fen()}"
            unique_id = hashlib.sha256(raw_data.encode()).hexdigest()

        # Export the game back to a text-based PGN file for safekeeping
        exporter = chess.pgn.StringExporter(headers=False, variations=False, comments=False)
        clean_pgn = game.accept(exporter)

        result = headers.get("Result", "*")
        winner = None

        if result == "1-0":
            winner = "White"
        elif result == "0-1":
            winner = "Black"
        elif result == "1/2-1/2":
            winner = "Draw"

        games_data.append({
            "unique_id": unique_id,
            "white_player": headers.get("White", "Unknown"),
            "black_player": headers.get("Black", "Unknown"),
            "result": result,
            "winner": winner,
            "date_played": datetime.strptime(headers.get("Date", None), "%Y.%m.%d").date(),
            "opening_name": headers.get("Opening", None),
            "time_control": headers.get("TimeControl", None),
            "pgn_content": clean_pgn,
        })

    return games_data