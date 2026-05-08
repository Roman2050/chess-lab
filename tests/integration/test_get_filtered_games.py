import pytest

from app.models.db import Game
from app.schemas.games import SortOrder
from app.services.game_queries import get_filtered_games


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_filtered_games_filters_and_paginates(async_session):
    async_session.add_all(
        [
            Game(
                unique_id="g1",
                white_player="Alice",
                black_player="Bob",
                result="1-0",
                winner="White",
                opening_name=None,
                time_control=None,
                pgn_content="1. e4 e5 1-0",
            ),
            Game(
                unique_id="g2",
                white_player="Carol",
                black_player="Alice",
                result="0-1",
                winner="Black",
                opening_name=None,
                time_control=None,
                pgn_content="1. d4 d5 0-1",
            ),
            Game(
                unique_id="g3",
                white_player="Eve",
                black_player="Mallory",
                result="1/2-1/2",
                winner="Draw",
                opening_name=None,
                time_control=None,
                pgn_content="1. c4 c5 1/2-1/2",
            ),
        ]
    )
    await async_session.commit()

    # Filter by player_name should match either white or black
    total, games = await get_filtered_games(
        async_session,
        limit=50,
        offset=0,
        sort_order=SortOrder.asc,
        player_name="Alice",
        winner=None,
    )
    assert total == 2
    assert {g.unique_id for g in games} == {"g1", "g2"}

    # Filter by winner
    total, games = await get_filtered_games(
        async_session,
        limit=50,
        offset=0,
        sort_order=SortOrder.asc,
        player_name=None,
        winner="Draw",
    )
    assert total == 1
    assert [g.unique_id for g in games] == ["g3"]

    # Pagination + sort: asc means by increasing id
    total, games = await get_filtered_games(
        async_session,
        limit=1,
        offset=1,
        sort_order=SortOrder.asc,
        player_name=None,
        winner=None,
    )
    assert total == 3
    assert len(games) == 1

