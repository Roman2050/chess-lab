import pytest

from app.services.db_manager import bulk_save_games


@pytest.mark.integration
@pytest.mark.asyncio
async def test_bulk_save_games_inserts_and_ignores_duplicates(async_session):
    games = [
        {
            "unique_id": "g1",
            "white_player": "A",
            "black_player": "B",
            "result": "1-0",
            "winner": "White",
            "opening_name": None,
            "time_control": None,
            "pgn_content": "1. e4 e5 1-0",
        },
        {
            "unique_id": "g2",
            "white_player": "C",
            "black_player": "D",
            "result": "0-1",
            "winner": "Black",
            "opening_name": None,
            "time_control": None,
            "pgn_content": "1. d4 d5 0-1",
        },
    ]

    stats1 = await bulk_save_games(async_session, games)
    assert stats1["saved_new"] == 2
    assert stats1["total_processed"] == 2

    # Repeat insert should be ignored due to unique_id constraint
    stats2 = await bulk_save_games(async_session, games)
    assert stats2["saved_new"] == 0
    assert stats2["total_processed"] == 2

