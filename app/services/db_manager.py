from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert
from app.models.db import Game


async def bulk_save_games(db: AsyncSession, games_data: list[dict]) -> dict:
    """Saves games to the database, ignoring existing ones (based on unique_id)."""
    if not games_data:
        return {"saved": 0, "ignored": 0}

    stmt = insert(Game).values(games_data)

    stmt = stmt.on_conflict_do_nothing(index_elements=['unique_id'])

    result = await db.execute(stmt)
    await db.commit()

    return {"saved_new": result.rowcount, "total_processed": len(games_data)}