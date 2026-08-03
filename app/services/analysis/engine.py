from __future__ import annotations

import io

import chess
import chess.engine
import chess.pgn

# Internal sentinel for "mate in N" — converts python-chess `MateGiven` /
# `Mate(N)` into a finite cp value so downstream cp_loss arithmetic stays
# simple. This is a contract between engine.py and classifier.py, not a
# user-facing tuning knob, so it intentionally stays out of Settings.
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
    """Sync UCI Stockfish wrapper for full-game analysis (MultiPV search).

    All tuning parameters are injected at construction time so this class
    stays decoupled from `app.config.settings` — callers (Celery task, tests)
    build the instance with whatever values they want. Keyword-only args
    after `path` prevent accidental positional reordering when adding more
    knobs later.
    """

    def __init__(
        self,
        path: str,
        *,
        depth: int = 20,
        multipv: int = 2,
        threads: int = 1,
        hash_mb: int = 128,
    ) -> None:
        self._path = path
        self._depth = depth
        self._multipv = multipv
        self._threads = threads
        self._hash_mb = hash_mb

    def _configure_uci_options(self, engine: chess.engine.SimpleEngine) -> None:
        """Apply per-engine UCI options (Threads, Hash) once after startup.

        Names match official Stockfish (case-sensitive). For UCI engines that
        don't expose a given option (forks, Lc0, custom builds) we silently
        skip it instead of failing — `engine.options` is the authoritative
        list of what `configure()` will accept.

        MultiPV is intentionally NOT set here: python-chess manages it
        automatically and rejects `configure({"MultiPV": N})` with an
        EngineError. It's passed per-call to `engine.analyse(..., multipv=N)`
        in `_analyse_position` instead.
        """
        overrides: dict[str, int] = {}
        if "Threads" in engine.options:
            overrides["Threads"] = self._threads
        if "Hash" in engine.options:
            overrides["Hash"] = self._hash_mb
        if overrides:
            engine.configure(overrides)

    def _analyse_position(
        self, engine: chess.engine.SimpleEngine, board: chess.Board
    ) -> tuple[int, str | None, int | None]:
        """Return the top-line eval/move and the second-line eval, if present.

        `best_move_uci` is the first move of the top-line PV in UCI notation
        (e.g. ``"e2e4"``), or `None` if Stockfish returned no PV for this
        position (mate / stalemate or a degraded info packet). Both evaluations
        are White-relative; `second_eval_cp` is `None` when no usable second PV
        exists.
        """
        infos = engine.analyse(
            board,
            chess.engine.Limit(depth=self._depth),
            multipv=self._multipv,
        )
        primary = infos[0] if isinstance(infos, list) else infos

        score = primary.get("score")
        eval_cp = _pov_to_white_cp(score) if score is not None else 0

        pv = primary.get("pv") or []
        best_move_uci = pv[0].uci() if pv else None

        second_eval_cp: int | None = None
        if isinstance(infos, list) and len(infos) > 1:
            second_score = infos[1].get("score")
            if second_score is not None:
                second_eval_cp = _pov_to_white_cp(second_score)

        return eval_cp, best_move_uci, second_eval_cp

    def analyse_game(self, pgn_content: str) -> list[dict]:
        """Analyse every game position once and return per-move evaluations."""
        game = chess.pgn.read_game(io.StringIO(pgn_content))
        if game is None:
            return []

        board = game.board()
        moves = list(game.mainline_moves())
        if not moves:
            return []

        out: list[dict] = []

        with chess.engine.SimpleEngine.popen_uci(self._path) as engine:
            self._configure_uci_options(engine)
            current_eval, current_best, current_second = self._analyse_position(
                engine, board
            )

            for ply, move in enumerate(moves, start=1):
                eval_before = current_eval
                best_move_uci = current_best
                second_eval_cp = current_second
                side = board.turn
                color = "White" if side == chess.WHITE else "Black"
                san = board.san(move)

                board.push(move)
                current_eval, current_best, current_second = self._analyse_position(
                    engine, board
                )

                out.append(
                    {
                        "ply": ply,
                        "san": san,
                        "color": color,
                        "eval_before": eval_before,
                        "eval_after": current_eval,
                        "best_move": best_move_uci,
                        "second_eval_cp": second_eval_cp,
                    }
                )

        return out
