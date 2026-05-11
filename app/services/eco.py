from __future__ import annotations

import json
import re
from pathlib import Path
from threading import Lock
from typing import Any

import chess

_MOVE_NUM = re.compile(r"^\d+\.$")
_DEFAULT_ECO_PATH = Path(__file__).resolve().parents[2] / "data" / "eco.json"

_eco_lookup: EcoLookup | None = None
_eco_lookup_lock = Lock()


def _fen_prefix(fen: str) -> str:
    parts = fen.split()
    return " ".join(parts[:4])


def _normalize_moves_line(moves_str: str) -> str:
    out: list[str] = []
    for raw in moves_str.split():
        t = raw.strip()
        if not t or t == "...":
            continue
        if _MOVE_NUM.match(t):
            continue
        out.append(t)
    return " ".join(out)


def _ply_count(norm: str) -> int:
    return len(norm.split()) if norm else 0


def _better_entry(
    existing: dict[str, Any] | None,
    eco: str,
    name: str,
    norm_moves: str,
) -> dict[str, Any]:
    new_ply = _ply_count(norm_moves)
    if existing is None:
        return {"eco": eco, "name": name, "_ply": new_ply}
    if new_ply > existing["_ply"]:
        return {"eco": eco, "name": name, "_ply": new_ply}
    return existing


class EcoLookup:
    """Local ECO opening lookup by FEN prefix (first four fields) or SAN move sequence."""

    def __init__(self, eco_path: str) -> None:
        path = Path(eco_path)
        with path.open(encoding="utf-8") as f:
            rows: list[dict[str, str]] = json.load(f)

        self._by_fen: dict[str, dict[str, Any]] = {}
        self._by_moves: dict[str, dict[str, Any]] = {}

        for row in rows:
            eco = row["eco"]
            name = row["name"]
            fen_key = _fen_prefix(row["fen"])
            norm = _normalize_moves_line(row["moves"])

            self._by_fen[fen_key] = _better_entry(self._by_fen.get(fen_key), eco, name, norm)
            self._by_moves[norm] = _better_entry(self._by_moves.get(norm), eco, name, norm)

    def lookup(self, board: chess.Board) -> dict[str, str] | None:
        fen_key = _fen_prefix(board.fen())
        hit = self._by_fen.get(fen_key)
        if hit is not None:
            return {"eco": hit["eco"], "name": hit["name"]}

        played = self._board_move_sans(board)
        for n in range(len(played), 0, -1):
            key = " ".join(played[:n])
            m = self._by_moves.get(key)
            if m is not None:
                return {"eco": m["eco"], "name": m["name"]}
        return None

    @staticmethod
    def _board_move_sans(board: chess.Board) -> list[str]:
        b = chess.Board()
        sans: list[str] = []
        for mv in board.move_stack:
            sans.append(b.san(mv))
            b.push(mv)
        return sans


def get_eco_lookup(eco_path: str | None = None) -> EcoLookup:
    global _eco_lookup
    if _eco_lookup is not None:
        return _eco_lookup
    with _eco_lookup_lock:
        if _eco_lookup is None:
            path = eco_path if eco_path is not None else str(_DEFAULT_ECO_PATH)
            _eco_lookup = EcoLookup(path)
        return _eco_lookup
