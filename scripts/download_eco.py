"""
Download pinned ECO opening lines from lichess-org/chess-openings and build data/eco.json.

Source is pinned by commit below and distributed under CC0-1.0.
"""

from __future__ import annotations

import csv
import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import chess
import chess.pgn

ECO_SOURCE_REPOSITORY = "https://github.com/lichess-org/chess-openings"
ECO_SOURCE_COMMIT = "4b8622759e7ae6f93f011cc6c83a3823401ab45e"
BASE = f"https://raw.githubusercontent.com/lichess-org/chess-openings/{ECO_SOURCE_COMMIT}"
TSV_FILES = ("a.tsv", "b.tsv", "c.tsv", "d.tsv", "e.tsv")


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "chess-lab-eco-download/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read().decode("utf-8")


def _movetext_to_fen(movetext: str) -> str | None:
    movetext = movetext.strip()
    if not movetext:
        return None
    wrapped = f'[Event "?"]\n\n{movetext}\n'
    game = chess.pgn.read_game(io.StringIO(wrapped))
    if game is None:
        return None
    board = chess.Board()
    try:
        for move in game.mainline_moves():
            board.push(move)
    except (ValueError, chess.IllegalMoveError, chess.InvalidMoveError, chess.AmbiguousMoveError):
        return None
    return board.fen()


def _iter_tsv_rows(text: str):
    reader = csv.reader(io.StringIO(text), delimiter="\t")
    for row in reader:
        if len(row) < 3:
            continue
        eco, name, pgn = row[0].strip(), row[1].strip(), row[2].strip()
        if eco.lower() == "eco" or not eco:
            continue
        yield eco, name, pgn


def main() -> int:
    root = _repo_root()
    out_path = root / "data" / "eco.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, str]] = []
    skipped = 0

    for fname in TSV_FILES:
        url = f"{BASE}/{fname}"
        try:
            body = _fetch(url)
        except urllib.error.URLError as e:
            print(f"Failed to download {url}: {e}", file=sys.stderr)
            return 1

        for eco, name, moves in _iter_tsv_rows(body):
            fen = _movetext_to_fen(moves)
            if fen is None:
                skipped += 1
                continue
            entries.append(
                {
                    "eco": eco,
                    "name": name,
                    "moves": moves,
                    "fen": fen,
                }
            )

    out_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(entries)} openings to {out_path}")
    if skipped:
        print(f"Skipped {skipped} rows (unparseable movetext)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
