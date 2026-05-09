from __future__ import annotations

import chess

# Thresholds (inclusive upper bounds) per ARCHITECTURE.md §5.3.
_BEST_MAX = 10
_EXCELLENT_MAX = 25
_GOOD_MAX = 50
_INACCURACY_MAX = 100
_MISTAKE_MAX = 300

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
    """
    if color == "White":
        loss = eval_before - eval_after
    else:
        loss = eval_after - eval_before
    return max(0, loss)


def _advantage_flags(
    white_peak_cp: int,
    black_peak_cp: int,
    final_eval_cp: int,
) -> dict[str, bool]:
    """Did either side reach a clear edge but fail to keep it?"""
    white_lost = (
        white_peak_cp >= _ADVANTAGE_THRESHOLD_CP
        and final_eval_cp < _RETAINED_THRESHOLD_CP
    )
    black_lost = (
        black_peak_cp <= -_ADVANTAGE_THRESHOLD_CP
        and final_eval_cp > -_RETAINED_THRESHOLD_CP
    )
    return {"white": white_lost, "black": black_lost}


def build_analysis_data(moves: list[dict]) -> dict:
    """Assemble the final `analysis_data` JSON from raw engine evaluations.

    Input format (from `StockfishEngine.analyse_game`):
        [{"ply", "san", "color", "eval_before", "eval_after"}, ...]

    Output matches ARCHITECTURE.md §5.3. Per §3.4, the FEN fields and
    `tactical_tags` are emitted **only** for inaccuracy/mistake/blunder moves;
    quiet moves stay lightweight.

    SAN is replayed on a `chess.Board` here (not in engine.py) so we can attach
    `fen_before` / `fen_after` to error moves without inflating the engine API.
    `is_only_move`, `best_move_engine`, and `tactical_tags` are populated with
    placeholder defaults — they will be filled in by Phase 3 (tactical
    detector + MultiPV gap analysis).
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

    for raw in moves:
        ply = int(raw["ply"])
        san = str(raw["san"])
        color = str(raw["color"])
        eval_before = int(raw["eval_before"])
        eval_after = int(raw["eval_after"])

        cp_loss = _cp_loss_for_move(color, eval_before, eval_after)
        classification = classify_move(cp_loss)

        # Capture FENs around the move; only attached for error moves below.
        fen_before = board.fen()
        try:
            move_obj = board.parse_san(san)
            board.push(move_obj)
        except (ValueError, chess.IllegalMoveError, chess.InvalidMoveError, chess.AmbiguousMoveError):
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
        }

        if classification in _ERROR_CLASSES:
            entry["is_only_move"] = False
            entry["best_move_engine"] = None
            entry["tactical_tags"] = []
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

    return {
        "summary": {
            "white_acpl": white_acpl,
            "black_acpl": black_acpl,
            "advantage_lost": _advantage_flags(
                white_peak_cp, black_peak_cp, final_eval_cp
            ),
        },
        "moves": enriched,
    }
