import pytest

from app.models.db import Game
from app.schemas.games import SortOrder
from app.services.aggregation.acpl import get_player_acpl
from app.services.aggregation.helpers import count_player_analyzed_games_sync
from app.services.game_queries import get_filtered_games
from app.services.report_repository import count_analyzed_games


def _analysis_data(color: str, cp_loss: int) -> dict:
    return {
        "summary": {
            "white_acpl": cp_loss,
            "black_acpl": cp_loss,
            "advantage_lost": {"white": False, "black": False},
        },
        "moves": [
            {
                "ply": 1,
                "move_num": 1,
                "color": color,
                "san": "e4",
                "piece": "P",
                "eval_before": 0,
                "eval_after": 0,
                "cp_loss": cp_loss,
                "classification": "best",
                "phase": "opening",
            }
        ],
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_filtered_games_filters_and_paginates(
    async_session,
    sync_session_factory,
):
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
                is_analyzed=True,
                analysis_status="completed",
                analysis_data=_analysis_data("White", 10),
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
                is_analyzed=True,
                analysis_status="completed",
                analysis_data=_analysis_data("Black", 20),
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

    # Player filtering is case-insensitive and matches either color.
    total, games = await get_filtered_games(
        async_session,
        limit=50,
        offset=0,
        sort_order=SortOrder.asc,
        player_name="aLiCe",
        winner=None,
    )
    assert total == 2
    assert {g.unique_id for g in games} == {"g1", "g2"}

    stats = await get_player_acpl(async_session, "aLiCe")
    assert stats["games_count"] == 2
    assert stats["acpl"] == 15.0
    assert await count_analyzed_games(async_session, "aLiCe") == 2

    with sync_session_factory() as session:
        assert count_player_analyzed_games_sync(session, "aLiCe") == 2

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

