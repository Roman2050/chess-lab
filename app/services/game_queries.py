from sqlalchemy import select, func, or_, desc, asc
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.db import Game 
from app.schemas.games import SortOrder

async def get_filtered_games(
    db: AsyncSession,
    limit: int,
    offset: int,
    sort_order: SortOrder,
    player_name: str | None = None,
    winner: str | None = None
) -> tuple[int, list[Game]]:

    stmt = select(Game)
    
    if player_name:
        name_lower = player_name.lower()
        stmt = stmt.where(
            or_(
                func.lower(Game.white_player) == name_lower,
                func.lower(Game.black_player) == name_lower,
            )
        )
    if winner:
        stmt = stmt.where(func.lower(Game.winner) == winner.lower())

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_count = await db.scalar(count_stmt)

    order_column = Game.id if sort_order == SortOrder.asc else desc(Game.id)
    stmt = stmt.order_by(order_column)

    stmt = stmt.offset(offset).limit(limit)

    result = await db.execute(stmt)
    games = result.scalars().all()

    return total_count, list(games)