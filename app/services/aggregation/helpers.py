from __future__ import annotations

from typing import Iterator

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, load_only

from app.models.db import Game
from app.services.analysis.classifier import CP_LOSS_CAP

# Everything the aggregations actually read, minus `pgn_content`: the raw PGN is
# dead weight here (moves are read from `analysis_data`, ARCHITECTURE.md §5.3),
# yet on a few hundred games it is the bulk of the bytes we'd pull over the wire.
# `analysis_data` stays loaded — every metric in this package is derived from it,
# and `compute_opening_stats` needs it even on the all-games fetch.
_STAT_COLUMNS = (
    Game.id,
    Game.unique_id,
    Game.white_player,
    Game.black_player,
    Game.result,
    Game.winner,
    Game.opening_name,
    Game.time_control,
    Game.date_played,
    Game.is_analyzed,
    Game.analysis_data,
)

# raiseload turns an accidental `game.pgn_content` into an immediate error
# instead of a silent per-row SELECT (sync) or a MissingGreenlet raised far from
# the cause (async). Same for the analysis_status family, which no aggregation
# reads — add a column here if that ever changes.
_STAT_LOAD_OPTIONS = (load_only(*_STAT_COLUMNS, raiseload=True),)


async def get_player_games(
    db: AsyncSession,
    player_name: str,
    time_control: str | None = None,
) -> list[Game]:
    """Fetch every game the player participated in, analyzed or not.

    Used by aggregations whose primary signal doesn't require engine output
    (win-rate, opening frequency). Caller is responsible for filtering on
    ``is_analyzed`` when it does — see :func:`get_player_analyzed_games` for
    the narrower variant.

    ``analysis_data`` is still loaded: ``compute_opening_stats`` reads per-move
    output for the analyzed subset. ``pgn_content`` is not (see
    :data:`_STAT_LOAD_OPTIONS`).
    """
    stmt = select(Game).where(
        or_(
            Game.white_player == player_name,
            Game.black_player == player_name,
        ),
    ).options(*_STAT_LOAD_OPTIONS)

    if time_control is not None:
        stmt = stmt.where(Game.time_control == time_control)

    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_player_analyzed_games(
    db: AsyncSession,
    player_name: str,
    time_control: str | None = None,
) -> list[Game]:
    """Fetch every analyzed game the player participated in.

    Returns games where the player appears on either side (white/black) and
    `is_analyzed` is True. Optionally narrows the result to a single time
    control bucket (e.g. ``"blitz"``).

    Player-color filtering happens downstream (see `resolve_player_color` /
    `iter_player_moves`) — ARCHITECTURE.md §3.1 keeps the engine output
    color-agnostic, so the per-player split is purely a read-side concern.

    Loads ``analysis_data`` but not ``pgn_content`` (see
    :data:`_STAT_LOAD_OPTIONS`).
    """
    stmt = select(Game).where(
        or_(
            Game.white_player == player_name,
            Game.black_player == player_name,
        ),
        Game.is_analyzed.is_(True),
    ).options(*_STAT_LOAD_OPTIONS)

    if time_control is not None:
        stmt = stmt.where(Game.time_control == time_control)

    result = await db.execute(stmt)
    return list(result.scalars().all())


def get_player_games_sync(
    db: Session,
    player_name: str,
    time_control: str | None = None,
) -> list[Game]:
    """Sync twin of :func:`get_player_games` for Celery tasks.

    Celery runs on a sync DB session (`.cursorrules` / ARCHITECTURE.md), so the
    async fetch above can't be reused there. Same query, same semantics, same
    column set — fetch every game the player played, analyzed or not.

    This is the single fetch behind the whole report context, so the deferred
    ``pgn_content`` matters most here: the task holds a session while it runs.
    """
    stmt = select(Game).where(
        or_(
            Game.white_player == player_name,
            Game.black_player == player_name,
        ),
    ).options(*_STAT_LOAD_OPTIONS)

    if time_control is not None:
        stmt = stmt.where(Game.time_control == time_control)

    return list(db.execute(stmt).scalars().all())


def get_player_analyzed_games_sync(
    db: Session,
    player_name: str,
    time_control: str | None = None,
) -> list[Game]:
    """Sync twin of :func:`get_player_analyzed_games` for Celery tasks."""
    stmt = select(Game).where(
        or_(
            Game.white_player == player_name,
            Game.black_player == player_name,
        ),
        Game.is_analyzed.is_(True),
    ).options(*_STAT_LOAD_OPTIONS)

    if time_control is not None:
        stmt = stmt.where(Game.time_control == time_control)

    return list(db.execute(stmt).scalars().all())


def count_player_analyzed_games_sync(db: Session, player_name: str) -> int:
    """Count the player's analyzed games without materializing them.

    The LLM report stage only needs a gate ("do we have enough analyzed games
    to bother?"), so a ``COUNT(*)`` is cheaper than pulling every row through
    :func:`get_player_analyzed_games_sync`.
    """
    stmt = (
        select(func.count())
        .select_from(Game)
        .where(
            or_(
                Game.white_player == player_name,
                Game.black_player == player_name,
            ),
            Game.is_analyzed.is_(True),
        )
    )

    return db.execute(stmt).scalar_one()


def resolve_player_color(game: Game, player_name: str) -> str:
    """Return ``"White"`` or ``"Black"`` for `player_name` in `game`.

    Raises `ValueError` when the player doesn't match either side. This should
    never happen if the caller filtered through `get_player_analyzed_games`,
    but we'd rather crash loudly than silently mis-attribute moves to the
    wrong color in the downstream report.
    """
    if game.white_player == player_name:
        return "White"
    if game.black_player == player_name:
        return "Black"
    raise ValueError(f"Player {player_name} not found in game {game.id}")


def iter_player_moves(game: Game, player_color: str) -> Iterator[dict]:
    """Yield every analysis move played by `player_color` in `game`.

    Tolerates games without analysis (``analysis_data is None``) or with a
    payload missing the ``"moves"`` key — both yield nothing. Streaming as a
    generator avoids copying potentially long move lists when callers only
    aggregate (e.g. ACPL sums).

    ``cp_loss`` is clamped to ``[0, CP_LOSS_CAP]`` on the way out. The source
    cap in ``classifier._cp_loss_for_move`` only applies to *new* analyses;
    games analysed before that fix still carry raw mate-inflated values (up to
    ±10000) in their stored JSONB, which would push ACPL past 1000. Clamping
    here sanitises old rows at read time without re-running Stockfish. A shallow
    copy is yielded so the ORM-attached ``analysis_data`` dict is never mutated.
    """
    analysis = game.analysis_data
    if not analysis:
        return
    moves = analysis.get("moves")
    if not moves:
        return
    for move in moves:
        if move.get("color") == player_color:
            raw_cp_loss = move.get("cp_loss", 0)
            clamped = min(max(0, raw_cp_loss), CP_LOSS_CAP)
            if clamped != raw_cp_loss:
                yield {**move, "cp_loss": clamped}
            else:
                yield move
