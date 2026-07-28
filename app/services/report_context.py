from __future__ import annotations

from sqlalchemy.orm import Session

from app.schemas.report import ReportContext, ReportInsights
from app.services.aggregation.accuracy import compute_accuracy_by_phase
from app.services.aggregation.errors import compute_error_patterns
from app.services.aggregation.helpers import get_player_games_sync
from app.services.aggregation.openings import compute_opening_stats
from app.services.aggregation.winprob import compute_player_wp_loss

# Overall-skill buckets keyed off mean win-probability loss (% of winning
# chances lost per move). A narrative hint for the report, NOT the official
# per-move cp_loss classification (see .cursorrules).
# Interim calibration from the real-player sample in sandbox.ipynb (Phase 6
# Chat 5). Revisit when the calibration dataset grows.
_WP_SKILL_STRONG_MAX = 2.5
_WP_SKILL_SOLID_MAX = 4.0
_WP_SKILL_INCONSISTENT_MAX = 6.0

# white vs black WP-loss must differ by at least this (percentage points)
# before we call one color a genuine weakness rather than normal noise.
COLOR_BIAS_THRESHOLD = 1.0

# A phase is only crowned strongest if it leads the next-best phase by at least
# this WP-loss margin (percentage points) — otherwise strongest_phase is None.
# Guards against declaring a phase a strength on noise.
PHASE_WP_BIAS_THRESHOLD = 1.0

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

    One DB round-trip: the analyzed subset is filtered in memory rather than
    re-queried, so the report never transfers the same ``analysis_data`` twice
    (it is by far the heaviest column on `games`).
    """
    games_all = get_player_games_sync(db, player_name)
    games_analyzed = [game for game in games_all if game.is_analyzed]

    wp = compute_player_wp_loss(games_analyzed, player_name)
    accuracy = compute_accuracy_by_phase(games_analyzed, player_name)
    errors = compute_error_patterns(games_analyzed, player_name)
    openings = compute_opening_stats(
        games_all, player_name, limit=_OPENING_STATS_LIMIT
    )

    played_dates = [g.date_played for g in games_all if g.date_played]
    last_game_played_at = max(played_dates) if played_dates else None

    insights = derive_insights(wp, accuracy, errors, openings)

    # Pydantic coerces the plain aggregation dicts into their nested models on
    # construction, so this doubles as validation of the aggregation output.
    return ReportContext(
        player=player_name,
        language=language,
        analyzed_games_count=len(games_analyzed),
        total_games_count=len(games_all),
        last_game_played_at=last_game_played_at,
        wp=wp,
        accuracy_by_phase=accuracy,
        openings=openings,
        errors=errors,
        insights=insights,
    )


def derive_insights(
    wp: dict,
    accuracy: dict[str, dict],
    errors: dict,
    openings: list[dict],
) -> ReportInsights:
    """Turn the raw aggregation dicts into deterministic narrative hints.

    Pure function: all the "smart" report logic lives here, never in the LLM.
    Every threshold is a module-level constant rather than a magic number.
    Move quality is expressed as win-probability loss (Phase 6); ``accuracy`` is
    kept for callers/parity but phase strength is now derived from ``wp``.
    """
    wp_by_phase = wp.get("wp_loss_by_phase", {})
    return ReportInsights(
        overall_skill=_overall_skill(wp.get("wp_loss")),
        weakest_phase=_extreme_phase(wp_by_phase, weakest=True),
        strongest_phase=_strongest_phase(wp_by_phase),
        weaker_color=_weaker_color(wp.get("wp_loss_by_color", {})),
        dominant_error_piece=_dominant_error_piece(errors),
        error_hotspot_moves=_error_hotspot_moves(errors),
        best_openings=_ranked_openings(openings, best=True),
        worst_openings=_ranked_openings(openings, best=False),
    )


def _overall_skill(wp_loss: float | None) -> str:
    """Bucket mean WP-loss into a coarse skill label; ``None`` → ``"inconsistent"``."""
    if wp_loss is None:
        return "inconsistent"
    if wp_loss <= _WP_SKILL_STRONG_MAX:
        return "strong"
    if wp_loss <= _WP_SKILL_SOLID_MAX:
        return "solid"
    if wp_loss <= _WP_SKILL_INCONSISTENT_MAX:
        return "inconsistent"
    return "weak"


def _extreme_phase(phase_values: dict[str, float | None], *, weakest: bool) -> str | None:
    """Phase with the highest (weakest) or lowest (strongest) WP-loss.

    Accepts a ``phase -> value`` mapping; only phases with a non-None value are
    considered. Returns ``None`` when no phase qualifies.
    """
    candidates = [
        (phase, value) for phase, value in phase_values.items() if value is not None
    ]
    if not candidates:
        return None
    chooser = max if weakest else min
    return chooser(candidates, key=lambda item: item[1])[0]


def _strongest_phase(phase_values: dict[str, float | None]) -> str | None:
    """Lowest-WP-loss phase, but only if it clears the significance margin.

    Guards against the "opening always looks strongest" artifact: the best phase
    must lead the next-best by at least :data:`PHASE_WP_BIAS_THRESHOLD`,
    otherwise the phases are too close to call and we return ``None``.
    """
    best = _extreme_phase(phase_values, weakest=False)
    if best is None:
        return None
    values = sorted(v for v in phase_values.values() if v is not None)
    if len(values) >= 2 and (values[1] - values[0]) < PHASE_WP_BIAS_THRESHOLD:
        return None
    return best


def _weaker_color(wp_loss_by_color: dict[str, float | None]) -> str | None:
    """Color whose WP-loss is meaningfully worse, or ``None`` if too close / missing."""
    white = wp_loss_by_color.get("white")
    black = wp_loss_by_color.get("black")
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
