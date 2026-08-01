from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import ANALYSIS_STATUS_CLAIMABLE, Game


async def get_unanalyzed_game_ids(db: AsyncSession, player_name: str) -> list[int]:
    """Return ids of the player's games a worker could still pick up.

    Filters on `analysis_status`, not `is_analyzed`: games currently being
    analyzed (`running`) would otherwise be re-enqueued on every batch call, and
    the worker would burn a claim on each of them just to discard it.

    Only ids are selected (never ORM objects) — the batch endpoint fans these
    out one-per-Celery-task, so the heavy `pgn_content`/`analysis_data` columns
    must not be loaded here.
    """
    stmt = select(Game.id).where(
        or_(
            func.lower(Game.white_player) == func.lower(player_name),
            func.lower(Game.black_player) == func.lower(player_name),
        ),
        Game.analysis_status.in_(ANALYSIS_STATUS_CLAIMABLE),
    )

    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_analysis_progress(db: AsyncSession, player_name: str) -> dict:
    """Total / analyzed / pending game counts for a player in a single query.

    Uses a conditional aggregate (FILTER) so both counts come back in one
    round-trip instead of two separate queries.
    """
    stmt = select(
        func.count().label("total"),
        func.count()
        .filter(Game.is_analyzed == True)  # noqa: E712 — SQL boolean comparison
        .label("analyzed"),
    ).where(
        or_(
            func.lower(Game.white_player) == func.lower(player_name),
            func.lower(Game.black_player) == func.lower(player_name),
        )
    )

    row = (await db.execute(stmt)).one()
    total = row.total
    analyzed = row.analyzed

    return {
        "total": total,
        "analyzed": analyzed,
        "pending": total - analyzed,
    }
