from __future__ import annotations

from sqlalchemy.orm import Session

from app.schemas.report import ReportContext, ReportInsights
from app.services.aggregation.acpl import compute_player_acpl
from app.services.aggregation.accuracy import compute_accuracy_by_phase
from app.services.aggregation.errors import compute_error_patterns
from app.services.aggregation.helpers import (
    get_player_analyzed_games_sync,
    get_player_games_sync,
)
from app.services.aggregation.openings import compute_opening_stats

# Overall-skill buckets keyed off mean ACPL. A narrative hint for the report,
# NOT the official per-move cp_loss classification (see .cursorrules).
_SKILL_STRONG_MAX = 20.0
_SKILL_SOLID_MAX = 40.0
_SKILL_INCONSISTENT_MAX = 70.0

# white vs black ACPL must differ by at least this (centipawns) before we call
# one color a genuine weakness rather than normal noise.
COLOR_BIAS_THRESHOLD = 8.0

# A piece is only flagged as the dominant error source if it accounts for at
# least this share of all the player's errors.
DOMINANT_ERROR_PCT = 25.0

# Openings with fewer games than this have win-rates too noisy to rank.
MIN_OPENING_GAMES = 3

# How many move numbers / openings to surface in the narrative hints.
_HOTSPOT_LIMIT = 3
_OPENING_LIMIT = 2

_OPENING_STATS_LIMIT = 10


def build_report_context(
    db: Session,
    player_name: str,
    language: str,
) -> ReportContext:
    """Assemble the full :class:`ReportContext` for one player (sync, no LLM).

    Reuses the Phase 4 pure aggregations on a sync session so it can run inside
    a Celery task. Win-rate / opening signals use *all* games; engine-derived
    metrics use only analyzed games. Deliberately does **not** call the LLM —
    this is purely the data-gathering stage.
    """
    games_all = get_player_games_sync(db, player_name)
    games_analyzed = get_player_analyzed_games_sync(db, player_name)

    acpl = compute_player_acpl(games_analyzed, player_name)
    accuracy = compute_accuracy_by_phase(games_analyzed, player_name)
    errors = compute_error_patterns(games_analyzed, player_name)
    openings = compute_opening_stats(
        games_all, player_name, limit=_OPENING_STATS_LIMIT
    )

    played_dates = [g.date_played for g in games_all if g.date_played]
    last_game_played_at = max(played_dates) if played_dates else None

    insights = derive_insights(acpl, accuracy, errors, openings)

    # Pydantic coerces the plain aggregation dicts into their nested models on
    # construction, so this doubles as validation of the aggregation output.
    return ReportContext(
        player=player_name,
        language=language,
        analyzed_games_count=len(games_analyzed),
        total_games_count=len(games_all),
        last_game_played_at=last_game_played_at,
        acpl=acpl,
        accuracy_by_phase=accuracy,
        openings=openings,
        errors=errors,
        insights=insights,
    )


def derive_insights(
    acpl: dict,
    accuracy: dict[str, dict],
    errors: dict,
    openings: list[dict],
) -> ReportInsights:
    """Turn the raw aggregation dicts into deterministic narrative hints.

    Pure function: all the "smart" report logic lives here, never in the LLM.
    Every threshold is a module-level constant rather than a magic number.
    """
    return ReportInsights(
        overall_skill=_overall_skill(acpl.get("acpl")),
        weakest_phase=_extreme_phase(accuracy, weakest=True),
        strongest_phase=_extreme_phase(accuracy, weakest=False),
        weaker_color=_weaker_color(acpl.get("acpl_by_color", {})),
        dominant_error_piece=_dominant_error_piece(errors),
        error_hotspot_moves=_error_hotspot_moves(errors),
        best_openings=_ranked_openings(openings, best=True),
        worst_openings=_ranked_openings(openings, best=False),
    )


def _overall_skill(acpl: float | None) -> str:
    """Bucket mean ACPL into a coarse skill label; ``None`` → ``"inconsistent"``."""
    if acpl is None:
        return "inconsistent"
    if acpl <= _SKILL_STRONG_MAX:
        return "strong"
    if acpl <= _SKILL_SOLID_MAX:
        return "solid"
    if acpl <= _SKILL_INCONSISTENT_MAX:
        return "inconsistent"
    return "weak"


def _extreme_phase(accuracy: dict[str, dict], *, weakest: bool) -> str | None:
    """Phase with the highest (weakest) or lowest (strongest) ACPL.

    Only phases the player actually reached (``moves_count > 0`` and a non-None
    ACPL) are considered; if none qualify, returns ``None``.
    """
    candidates = [
        (phase, stats["acpl"])
        for phase, stats in accuracy.items()
        if stats.get("moves_count", 0) > 0 and stats.get("acpl") is not None
    ]
    if not candidates:
        return None
    chooser = max if weakest else min
    return chooser(candidates, key=lambda item: item[1])[0]


def _weaker_color(acpl_by_color: dict[str, float | None]) -> str | None:
    """Color whose ACPL is meaningfully worse, or ``None`` if too close / missing."""
    white = acpl_by_color.get("white")
    black = acpl_by_color.get("black")
    if white is None or black is None:
        return None
    if abs(white - black) < COLOR_BIAS_THRESHOLD:
        return None
    return "white" if white > black else "black"


def _dominant_error_piece(errors: dict) -> str | None:
    """Most error-prone piece, only if it clears the dominance threshold."""
    by_piece = errors.get("errors_by_piece", [])
    if not by_piece:
        return None
    top = by_piece[0]
    if top["error_pct"] < DOMINANT_ERROR_PCT:
        return None
    return top["piece_name"]


def _error_hotspot_moves(errors: dict) -> list[int]:
    """Top move numbers where errors cluster (already sorted upstream)."""
    by_move = errors.get("errors_by_move_number", [])
    return [row["move_num"] for row in by_move[:_HOTSPOT_LIMIT]]


def _ranked_openings(openings: list[dict], *, best: bool) -> list[str]:
    """Names of the best- or worst-performing openings by win-rate.

    Openings below :data:`MIN_OPENING_GAMES` are excluded — their win-rates
    swing too hard on a handful of games to rank meaningfully.
    """
    eligible = [o for o in openings if o["games_count"] >= MIN_OPENING_GAMES]
    ranked = sorted(eligible, key=lambda o: o["win_rate"], reverse=best)
    return [o["opening_name"] for o in ranked[:_OPENING_LIMIT]]
