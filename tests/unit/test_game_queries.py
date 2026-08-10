from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.schemas.games import SortOrder
from app.services.game_queries import get_filtered_games

PLAYER = "hero"


class _FakeDb:
    """AsyncSession stand-in that records the statements it is handed.

    `get_filtered_games` runs a COUNT through `scalar()` and the page itself
    through `execute()`, so both are captured separately — the assertions below
    only care about the page query.
    """

    def __init__(self) -> None:
        self.executed: list = []
        self.scalared: list = []

    async def scalar(self, stmt):
        self.scalared.append(stmt)
        return 3

    async def execute(self, stmt):
        self.executed.append(stmt)
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        return result


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


@pytest.mark.unit
async def test_filtered_games_defers_heavy_columns() -> None:
    """The page query must not read pgn_content / analysis_data.

    `GameSummary` exposes neither, so loading them just moves bytes (and TOAST
    reads) for nothing — up to 100 rows per request.
    """
    db = _FakeDb()

    await get_filtered_games(
        db,
        limit=50,
        offset=0,
        sort_order=SortOrder.desc,
        player_name=None,
        winner=None,
    )

    sql = _sql(db.executed[0])

    assert "pgn_content" not in sql
    assert "analysis_data" not in sql


@pytest.mark.unit
async def test_filtered_games_still_selects_summary_columns() -> None:
    """Every column `GameSummary` serializes is still in the SELECT list."""
    db = _FakeDb()

    await get_filtered_games(
        db,
        limit=50,
        offset=0,
        sort_order=SortOrder.desc,
        player_name=None,
        winner=None,
    )

    sql = _sql(db.executed[0])

    for column in (
        "games.id",
        "games.unique_id",
        "games.white_player",
        "games.black_player",
        "games.result",
        "games.winner",
        "games.opening_name",
        "games.time_control",
        "games.date_played",
    ):
        assert column in sql


@pytest.mark.unit
async def test_filtered_games_filters_and_paginates_in_sql() -> None:
    """Regression guard: pruning columns didn't disturb the WHERE / LIMIT."""
    db = _FakeDb()

    total, games = await get_filtered_games(
        db,
        limit=10,
        offset=20,
        sort_order=SortOrder.asc,
        player_name=PLAYER,
        winner="Draw",
    )

    assert total == 3
    assert games == []

    sql = _sql(db.executed[0])

    assert "lower(games.white_player) = lower('hero')" in sql
    assert "lower(games.black_player) = lower('hero')" in sql
    assert "lower(games.winner) = 'draw'" in sql
    assert "ORDER BY games.id" in sql
    assert "LIMIT 10" in sql
    assert "OFFSET 20" in sql
