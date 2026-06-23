from __future__ import annotations

from typing import Iterator

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import Game


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
    """
    stmt = select(Game).where(
        or_(
            Game.white_player == player_name,
            Game.black_player == player_name,
        ),
    )

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
    """
    stmt = select(Game).where(
        or_(
            Game.white_player == player_name,
            Game.black_player == player_name,
        ),
        Game.is_analyzed.is_(True),
    )

    if time_control is not None:
        stmt = stmt.where(Game.time_control == time_control)

    result = await db.execute(stmt)
    return list(result.scalars().all())


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
    """
    analysis = game.analysis_data
    if not analysis:
        return
    moves = analysis.get("moves")
    if not moves:
        return
    for move in moves:
        if move.get("color") == player_color:
            yield move
