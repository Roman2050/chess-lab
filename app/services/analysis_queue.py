from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import Game


async def get_unanalyzed_game_ids(db: AsyncSession, player_name: str) -> list[int]:
    """Return ids of the player's games that have not been analyzed yet.

    Only ids are selected (never ORM objects) — the batch endpoint fans these
    out one-per-Celery-task, so the heavy `pgn_content`/`analysis_data` columns
    must not be loaded here.
    """
    name_lower = player_name.lower()
    stmt = select(Game.id).where(
        or_(
            func.lower(Game.white_player) == name_lower,
            func.lower(Game.black_player) == name_lower,
        ),
        Game.is_analyzed == False,  # noqa: E712 — SQL boolean comparison, not Python identity
    )

    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_analysis_progress(db: AsyncSession, player_name: str) -> dict:
    """Total / analyzed / pending game counts for a player in a single query.

    Uses a conditional aggregate (FILTER) so both counts come back in one
    round-trip instead of two separate queries.
    """
    name_lower = player_name.lower()
    stmt = select(
        func.count().label("total"),
        func.count()
        .filter(Game.is_analyzed == True)  # noqa: E712 — SQL boolean comparison
        .label("analyzed"),
    ).where(
        or_(
            func.lower(Game.white_player) == name_lower,
            func.lower(Game.black_player) == name_lower,
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
