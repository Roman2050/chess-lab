from __future__ import annotations

import io

import chess
import chess.engine
import chess.pgn

_DEPTH = 20
_MULTIPV_LINES = 2
_MATE_CP = 10000


def _pov_to_white_cp(pov: chess.engine.PovScore) -> int:
    """Centipawns from White's perspective; mate scores become ±_MATE_CP."""
    w = pov.white()
    if w is chess.engine.MateGiven:
        return _MATE_CP
    mate_plies = w.mate()
    if mate_plies is not None:
        return _MATE_CP if mate_plies > 0 else -_MATE_CP
    cp = w.score()
    if cp is None:
        return 0
    return cp


class StockfishEngine:
    """Sync UCI Stockfish wrapper for full-game analysis (MultiPV search)."""

    def __init__(self, path: str) -> None:
        self._path = path

    def _best_eval_cp(self, engine: chess.engine.SimpleEngine, board: chess.Board) -> int:
        infos = engine.analyse(
            board,
            chess.engine.Limit(depth=_DEPTH),
            multipv=_MULTIPV_LINES,
        )
        primary = infos[0] if isinstance(infos, list) else infos
        score = primary.get("score")
        if score is None:
            return 0
        return _pov_to_white_cp(score)

    def analyse_game(self, pgn_content: str) -> list[dict]:
        game = chess.pgn.read_game(io.StringIO(pgn_content))
        if game is None:
            return []

        board = game.board()
        moves = list(game.mainline_moves())
        out: list[dict] = []

        with chess.engine.SimpleEngine.popen_uci(self._path) as engine:
            engine.configure({"MultiPV": _MULTIPV_LINES})

            for ply, move in enumerate(moves, start=1):
                eval_before = self._best_eval_cp(engine, board)
                side = board.turn
                color = "White" if side == chess.WHITE else "Black"
                san = board.san(move)

                board.push(move)
                eval_after = self._best_eval_cp(engine, board)

                out.append(
                    {
                        "ply": ply,
                        "san": san,
                        "color": color,
                        "eval_before": eval_before,
                        "eval_after": eval_after,
                    }
                )

        return out
