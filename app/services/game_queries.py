from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.models.db import Game
from app.schemas.games import SortOrder

# Exactly the columns GameSummary exposes (plus is_analyzed, a cheap boolean the
# list view may grow into). pgn_content and analysis_data are the two heavy
# columns on `games` and are never serialized here, so they stay unloaded.
_SUMMARY_COLUMNS = (
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
)


async def get_filtered_games(
    db: AsyncSession,
    limit: int,
    offset: int,
    sort_order: SortOrder,
    player_name: str | None = None,
    winner: str | None = None,
) -> tuple[int, list[Game]]:

    stmt = select(Game)

    if player_name:
        stmt = stmt.where(
            or_(
                func.lower(Game.white_player) == func.lower(player_name),
                func.lower(Game.black_player) == func.lower(player_name),
            )
        )
    if winner:
        stmt = stmt.where(func.lower(Game.winner) == winner.lower())

    # Built before load_only on purpose: loader options don't propagate into
    # .subquery(), so the COUNT is unaffected either way — keeping it here makes
    # that independence obvious instead of looking like an oversight.
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_count = await db.scalar(count_stmt)

    order_column = Game.id if sort_order == SortOrder.asc else desc(Game.id)
    stmt = stmt.order_by(order_column)

    stmt = stmt.offset(offset).limit(limit)

    # raiseload: touching pgn_content/analysis_data on these instances is a bug,
    # and on an AsyncSession a lazy re-fetch would blow up with MissingGreenlet
    # far from the cause. Fail loudly at the access site instead.
    stmt = stmt.options(load_only(*_SUMMARY_COLUMNS, raiseload=True))

    result = await db.execute(stmt)
    games = result.scalars().all()

    return total_count, list(games)
