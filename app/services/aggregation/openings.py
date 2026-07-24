from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import Game
from app.services.aggregation.helpers import (
    get_player_games,
    iter_player_moves,
    resolve_player_color,
)
from app.services.aggregation.winprob import move_wp_loss


async def get_opening_stats(
    db: AsyncSession,
    player_name: str,
    limit: int = 10,
) -> list[dict]:
    """Aggregate per-opening stats for `player_name`, top `limit` by volume.

    Win-rate is the headline signal here, and it doesn't need engine output,
    so we deliberately pull **all** of the player's games (not just analyzed
    ones) — otherwise a freshly imported batch of unanalyzed games would
    silently disappear from the player's opening repertoire until the
    Stockfish queue catches up.

    The secondary signal is now twofold: ``acpl_in_opening`` (raw centipawn
    loss) and ``wp_loss_in_opening`` (win-probability loss, §3.6) — each a
    flat per-move mean over moves tagged ``phase == "opening"``, restricted to
    analyzed games; WP additionally excludes positions outside the calibrated
    live-position window. Mirrors :func:`get_accuracy_by_phase`'s "flat
    per-move mean" choice — many openings have few analyzed games, and a
    per-game average would collapse to noise on tiny denominators. Openings
    without eligible values report the corresponding metric as ``None`` (not
    ``0``), so the frontend can distinguish missing data from perfect play.

    Games with ``opening_name`` NULL or empty are dropped: they're either
    pre-ECO-enrichment artifacts (see ARCHITECTURE.md §3.3) or malformed
    PGNs, and bucketing them under a synthetic "Unknown" label would mix
    unrelated games into a meaningless aggregate row.
    """
    games = await get_player_games(db, player_name)
    return compute_opening_stats(games, player_name, limit)


def compute_opening_stats(
    games: list[Game],
    player_name: str,
    limit: int = 10,
) -> list[dict]:
    """Pure aggregation core of :func:`get_opening_stats` (no DB access).

    Split out so Celery's sync report stage can feed pre-fetched games in
    without an async session — mirrors the ``compute_*`` / ``get_*`` divide in
    ``acpl.py`` / ``errors.py``. See :func:`get_opening_stats` for the metric
    rationale (all-games win-rate, analyzed-only ACPL, NULL openings dropped).
    """
    buckets: dict[str, _OpeningBucket] = defaultdict(_OpeningBucket)

    for game in games:
        opening = game.opening_name
        if not opening:
            continue

        bucket = buckets[opening]
        bucket.games_count += 1

        color = resolve_player_color(game, player_name)
        _tally_outcome(bucket, game, color)

        if game.is_analyzed:
            bucket.analyzed_games_count += 1
            for move in iter_player_moves(game, color):
                if move.get("phase") == "opening":
                    bucket.opening_cp_losses.append(move["cp_loss"])
                    wp = move_wp_loss(move)
                    if wp is not None:
                        bucket.opening_wp_losses.append(wp)

    rows = [_bucket_to_row(name, bucket) for name, bucket in buckets.items()]
    rows.sort(key=lambda row: row["games_count"], reverse=True)
    return rows[:limit]


@dataclass
class _OpeningBucket:
    """Mutable accumulator for one ``opening_name`` group.

    Kept private to this module — it's an implementation detail of the
    two-pass aggregation (one pass over games, then a final shape-flip into
    the public dict schema). A dataclass beats a bare dict here because the
    set of counters is fixed and typos in keys would silently zero out
    metrics.
    """

    games_count: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    analyzed_games_count: int = 0
    opening_cp_losses: list[int] = field(default_factory=list)
    opening_wp_losses: list[float] = field(default_factory=list)


def _tally_outcome(bucket: _OpeningBucket, game: Game, player_color: str) -> None:
    """Increment the win/draw/loss counter for one game.

    ``winner`` follows the DB convention ``"White" | "Black" | "Draw"`` (see
    ARCHITECTURE.md §5.1). Anything else — unfinished imports, stray nulls —
    is treated as "no result recorded" and silently dropped from the W/D/L
    split rather than miscounted; the game still contributes to
    ``games_count`` so the totals stay reconcilable.
    """
    winner = game.winner
    if winner == "Draw":
        bucket.draws += 1
    elif winner == player_color:
        bucket.wins += 1
    elif winner in ("White", "Black"):
        bucket.losses += 1


def _bucket_to_row(opening_name: str, bucket: _OpeningBucket) -> dict:
    """Flatten one accumulator into the public response shape."""
    if bucket.opening_cp_losses:
        acpl_in_opening: float | None = round(
            sum(bucket.opening_cp_losses) / len(bucket.opening_cp_losses), 1
        )
    else:
        acpl_in_opening = None

    if bucket.opening_wp_losses:
        wp_loss_in_opening: float | None = round(
            sum(bucket.opening_wp_losses) / len(bucket.opening_wp_losses), 2
        )
    else:
        wp_loss_in_opening = None

    return {
        "opening_name": opening_name,
        "games_count": bucket.games_count,
        "wins": bucket.wins,
        "draws": bucket.draws,
        "losses": bucket.losses,
        "win_rate": round(bucket.wins / bucket.games_count * 100, 1),
        "acpl_in_opening": acpl_in_opening,
        "wp_loss_in_opening": wp_loss_in_opening,
        "analyzed_games_count": bucket.analyzed_games_count,
    }
