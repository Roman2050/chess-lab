from __future__ import annotations

import chess

from app.services.analysis.phase import detect_phase
from app.services.analysis.tactical import detect_tactical_tags

# Thresholds (inclusive upper bounds) per ARCHITECTURE.md §5.3.
_BEST_MAX = 10
_EXCELLENT_MAX = 25
_GOOD_MAX = 50
_INACCURACY_MAX = 100
_MISTAKE_MAX = 300

# Upper bound on a single move's cp_loss. Mate scores are encoded as ±10000 cp
# (engine.py `_MATE_CP`), so a move that walks into — or throws away — a forced
# mate would otherwise contribute ~10000 to the average and blow ACPL past 1000,
# making a strong player look terrible. Past ~10 pawns the position is already
# decided and the exact magnitude carries no skill signal, so we clamp here.
# This does NOT touch classification: everything over 300 is already "blunder".
CP_LOSS_CAP = 1000

# Minimum gap between the best and second-best lines for a meaningful
# "only move" signal. This is provisional and can be calibrated later.
ONLY_MOVE_GAP_CP = 200

_ERROR_CLASSES: frozenset[str] = frozenset({"inaccuracy", "mistake", "blunder"})

# Heuristic for `summary.advantage_lost`: a side held a meaningful edge
# (>= ±200 cp) at some point but did not retain it (final eval falls below
# ±100 cp from its perspective). Tuned to be simple, not exhaustive.
_ADVANTAGE_THRESHOLD_CP = 200
_RETAINED_THRESHOLD_CP = 100


def classify_move(cp_loss: int) -> str:
    """Map centipawn loss to a move-quality label.

    See ARCHITECTURE.md §5.3 for the table:
        0–10 best, 11–25 excellent, 26–50 good,
        51–100 inaccuracy, 101–300 mistake, 300+ blunder.
    """
    if cp_loss <= _BEST_MAX:
        return "best"
    if cp_loss <= _EXCELLENT_MAX:
        return "excellent"
    if cp_loss <= _GOOD_MAX:
        return "good"
    if cp_loss <= _INACCURACY_MAX:
        return "inaccuracy"
    if cp_loss <= _MISTAKE_MAX:
        return "mistake"
    return "blunder"


def _piece_from_san(san: str) -> str:
    """Single-letter piece code for the moving piece, derived from SAN."""
    if not san:
        return "P"
    # Castling uses king notation in both encodings.
    if san.startswith("O-O") or san.startswith("0-0"):
        return "K"
    head = san[0]
    if head in ("K", "Q", "R", "B", "N"):
        return head
    return "P"


def _cp_loss_for_move(color: str, eval_before: int, eval_after: int) -> int:
    """Centipawn loss from the moving side's POV.

    `eval_before` / `eval_after` are White-relative (see engine.py). A White
    move is bad when the score drops; a Black move is bad when it rises.
    Negative deltas (a move evaluated higher than the prior best line) are
    clamped to 0 — engine fluctuations should not produce "negative loss".

    The result is also clamped from above to `CP_LOSS_CAP`: mate-bearing moves
    (eval ±10000) would otherwise dominate any ACPL average. See `CP_LOSS_CAP`.
    """
    if color == "White":
        loss = eval_before - eval_after
    else:
        loss = eval_after - eval_before
    return min(max(0, loss), CP_LOSS_CAP)


def _is_only_move(
    color: str,
    best_eval_cp: int,
    second_eval_cp: int | None,
) -> bool:
    """Whether the best MultiPV line is clearly superior for the mover.

    Evaluations are White-relative, so the gap direction reverses for Black.
    A missing second line returns `False`: positions with only one legal reply
    are trivially forced and do not provide a useful "only move" signal.
    """
    if second_eval_cp is None:
        return False
    if color == "White":
        gap = best_eval_cp - second_eval_cp
    else:
        gap = second_eval_cp - best_eval_cp
    return gap >= ONLY_MOVE_GAP_CP


def _parse_best_move(value: object, board: chess.Board) -> chess.Move | None:
    """Coerce an engine `best_move` payload (UCI string) into a legal `chess.Move`.

    Returns `None` when the payload is missing, malformed, or doesn't correspond
    to a legal move in `board` — the tactical detector tolerates `None` best
    moves (heuristics that need it simply opt out).
    """
    if value is None:
        return None
    uci = str(value).strip()
    if not uci:
        return None
    try:
        move = chess.Move.from_uci(uci)
    except (ValueError, chess.InvalidMoveError):
        return None
    return move if move in board.legal_moves else None


def _advantage_flags(
    white_peak_cp: int,
    black_peak_cp: int,
    final_eval_cp: int,
) -> dict[str, bool]:
    """Did either side reach a clear edge but fail to keep it?"""
    white_lost = white_peak_cp >= _ADVANTAGE_THRESHOLD_CP and final_eval_cp < _RETAINED_THRESHOLD_CP
    black_lost = (
        black_peak_cp <= -_ADVANTAGE_THRESHOLD_CP and final_eval_cp > -_RETAINED_THRESHOLD_CP
    )
    return {"white": white_lost, "black": black_lost}


def build_analysis_data(moves: list[dict]) -> dict:
    """Assemble the final `analysis_data` JSON from raw engine evaluations.

    Input format (from `StockfishEngine.analyse_game`):
        [{"ply", "san", "color", "eval_before", "eval_after",
          "best_move", "second_eval_cp"}, ...]

    Output matches ARCHITECTURE.md §5.3. Per §3.4, the FEN fields,
    `best_move_engine`, and `tactical_tags` are emitted **only** for
    inaccuracy/mistake/blunder moves; quiet moves stay lightweight.

    SAN is replayed on a `chess.Board` here (not in engine.py) so we can attach
    `fen_before` / `fen_after` to error moves without inflating the engine API.
    The engine ships `best_move` as UCI (e.g. ``"e2e4"``); we convert it to SAN
    on `board_before` for storage. `second_eval_cp` is used only to derive
    `is_only_move` and is never included in the stored `analysis_data`.
    """
    board = chess.Board()
    enriched: list[dict] = []

    white_loss_sum = 0
    white_count = 0
    black_loss_sum = 0
    black_count = 0

    white_peak_cp = 0
    black_peak_cp = 0
    final_eval_cp = 0

    # Track the last ply we saw each phase on so the summary can expose phase
    # boundaries without a second pass over `enriched`. `middlegame_end_ply`
    # falls back to `opening_end_ply` when no middlegame ever materialised
    # (e.g. an early queen trade snaps opening → endgame).
    opening_end_ply = 0
    middlegame_end_ply = 0

    for raw in moves:
        ply = int(raw["ply"])
        san = str(raw["san"])
        color = str(raw["color"])
        eval_before = int(raw["eval_before"])
        eval_after = int(raw["eval_after"])
        raw_second_eval = raw.get("second_eval_cp")
        second_eval_cp = int(raw_second_eval) if raw_second_eval is not None else None

        cp_loss = _cp_loss_for_move(color, eval_before, eval_after)
        classification = classify_move(cp_loss)

        # Capture FENs around the move; only attached for error moves below.
        fen_before = board.fen()
        board_before = board.copy(stack=False)
        # Phase is classified from `board_before`: the move is judged in the
        # context of the position it was played from, not the resulting one.
        phase = detect_phase(board_before, ply)
        try:
            move_obj = board.parse_san(san)
            board.push(move_obj)
        except (
            ValueError,
            chess.IllegalMoveError,
            chess.InvalidMoveError,
            chess.AmbiguousMoveError,
        ):
            # Defensive: if SAN is unparseable we still emit the entry but
            # stop trying to advance the board to avoid cascading errors.
            move_obj = None
        fen_after = board.fen()

        entry: dict = {
            "ply": ply,
            "move_num": (ply + 1) // 2,
            "color": color,
            "san": san,
            "piece": _piece_from_san(san),
            "eval_before": eval_before,
            "eval_after": eval_after,
            "cp_loss": cp_loss,
            "classification": classification,
            "phase": phase,
        }

        if phase == "opening":
            opening_end_ply = ply
        elif phase == "middlegame":
            middlegame_end_ply = ply

        if classification in _ERROR_CLASSES:
            best_move_obj = _parse_best_move(raw.get("best_move"), board_before)
            best_move_san = board_before.san(best_move_obj) if best_move_obj is not None else None

            tactical_tags: list[str] = []
            if move_obj is not None:
                # `board` is currently at the post-move position; copy it so
                # detectors that internally push/pop (e.g. missed_threat) can't
                # disturb our replay state.
                tactical_tags = detect_tactical_tags(
                    board_before,
                    board.copy(stack=False),
                    move_obj,
                    best_move_obj,
                )

            entry["is_only_move"] = _is_only_move(
                color,
                eval_before,
                second_eval_cp,
            )
            entry["best_move_engine"] = best_move_san
            entry["tactical_tags"] = tactical_tags
            entry["fen_before"] = fen_before
            entry["fen_after"] = fen_after

        enriched.append(entry)

        if color == "White":
            white_loss_sum += cp_loss
            white_count += 1
        else:
            black_loss_sum += cp_loss
            black_count += 1

        white_peak_cp = max(white_peak_cp, eval_before, eval_after)
        black_peak_cp = min(black_peak_cp, eval_before, eval_after)
        final_eval_cp = eval_after

    white_acpl = round(white_loss_sum / white_count) if white_count else 0
    black_acpl = round(black_loss_sum / black_count) if black_count else 0

    # If the game never entered middlegame (e.g. opening → endgame via an
    # early queen trade) we collapse `middlegame_end_ply` onto the opening
    # boundary so consumers don't have to special-case "missing" phases.
    if middlegame_end_ply == 0:
        middlegame_end_ply = opening_end_ply

    return {
        "summary": {
            "white_acpl": white_acpl,
            "black_acpl": black_acpl,
            "advantage_lost": _advantage_flags(white_peak_cp, black_peak_cp, final_eval_cp),
            "phase_boundaries": {
                "opening_end_ply": opening_end_ply,
                "middlegame_end_ply": middlegame_end_ply,
            },
        },
        "moves": enriched,
    }
