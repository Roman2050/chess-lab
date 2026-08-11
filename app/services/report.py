from __future__ import annotations

from enum import Enum

from app.models.db import PlayerReport


class ReportAction(str, Enum):
    """Outcome of the report (re)generation decision."""

    GENERATE = "generate"
    UP_TO_DATE = "up_to_date"
    INSUFFICIENT_GAMES = "insufficient_games"
    ALREADY_GENERATING = "already_generating"


def decide_report_action(
    current_analyzed: int,
    report: PlayerReport | None,
    threshold: int,
    *,
    generation_is_stale: bool = False,
) -> ReportAction:
    """Decide whether to (re)generate a report — pure, no DB access.

    Mirrors the decision table in the Phase 5 design (key decisions §7):
    regeneration is driven purely by the *count* of analyzed games, never by
    date. ``last_game_played_at`` is informational and plays no part here.

    ``generation_is_stale`` marks a ``generating`` row whose lease has expired:
    the worker that claimed it died, and nothing else will ever move it on, so
    the row is treated as not in flight and the ordinary count rules apply.
    """
    if report is not None and report.status == "generating" and not generation_is_stale:
        return ReportAction.ALREADY_GENERATING

    # No usable report yet (missing row, or a deleted/unfinished text): the
    # decision is simply whether enough analyzed games exist for a first report.
    if report is None or report.report_text is None:
        if current_analyzed < threshold:
            return ReportAction.INSUFFICIENT_GAMES
        return ReportAction.GENERATE

    delta = current_analyzed - report.analyzed_games_count
    if delta >= threshold:
        return ReportAction.GENERATE
    return ReportAction.UP_TO_DATE
