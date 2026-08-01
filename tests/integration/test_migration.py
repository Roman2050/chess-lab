import hashlib
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import text

STARTPOS_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _load_migration(filename: str, module_name: str):
    repo_root = Path(__file__).resolve().parents[2]
    migration_path = repo_root / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(module_name, migration_path)
    migration_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration_mod)
    return migration_mod


@pytest.mark.integration
@pytest.mark.asyncio
async def test_migration_rehashes_custom_rows_only(async_session, sync_session_factory):
    white = "Alice"
    black = "Bob"
    date_str = "2026.07.30"
    result_str = "1-0"
    clean_pgn = "1. e4 e5 1-0"

    # Old contract hash calculation
    old_raw = f"{white}{black}{date_str}{STARTPOS_FEN}"
    old_custom_hash = hashlib.sha256(old_raw.encode("utf-8")).hexdigest()

    # New contract expected hash
    new_raw = "|".join([white, black, date_str, result_str, clean_pgn])
    expected_new_hash = hashlib.sha256(new_raw.encode("utf-8")).hexdigest()

    lichess_id = "abcdef12"

    # Insert rows into database
    await async_session.execute(
        text(
            "INSERT INTO games (unique_id, white_player, black_player, result, winner, "
            "date_played, pgn_content, is_analyzed, analysis_status) "
            "VALUES (:custom_id, :w, :b, :res, 'White', '2026-07-30', :pgn, false, 'pending'), "
            "(:lichess_id, :w, :b, :res, 'White', '2026-07-30', :pgn, false, 'pending')"
        ),
        {
            "custom_id": old_custom_hash,
            "lichess_id": lichess_id,
            "w": white,
            "b": black,
            "res": result_str,
            "pgn": clean_pgn,
        },
    )
    await async_session.commit()

    migration_mod = _load_migration(
        "d8f2e3a4b5c6_rehash_custom_pgn_unique_id.py",
        "migration_d8f2e3a4b5c6",
    )

    # Execute migration upgrade logic using a sync session bind
    with sync_session_factory() as sync_session:
        with patch("alembic.op.get_bind", return_value=sync_session):
            migration_mod.upgrade()
            sync_session.commit()

    # Verify custom row unique_id was updated to expected_new_hash
    res_custom = await async_session.execute(
        text("SELECT unique_id FROM games WHERE pgn_content = :pgn AND unique_id != :lichess_id"),
        {"pgn": clean_pgn, "lichess_id": lichess_id},
    )
    custom_row_id = res_custom.scalar_one()
    assert custom_row_id == expected_new_hash

    # Verify lichess row unique_id remained untouched
    res_lichess = await async_session.execute(
        text("SELECT unique_id FROM games WHERE unique_id = :lichess_id"),
        {"lichess_id": lichess_id},
    )
    assert res_lichess.scalar_one() == lichess_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_case_insensitive_migration_rejects_report_duplicates(async_session):
    migration_mod = _load_migration(
        "e2f4a6b8c0d1_case_insensitive_player_identity.py",
        "migration_e2f4a6b8c0d1",
    )

    try:
        await async_session.execute(
            text("DROP INDEX uq_player_reports_player_lang_lower")
        )
        await async_session.execute(
            text(
                """
                INSERT INTO player_reports (player_name, language)
                VALUES ('MagnusCarlsen', 'en'), ('magnuscarlsen', 'en')
                """
            )
        )

        def _run_upgrade(sync_session) -> None:
            with patch("alembic.op.get_bind", return_value=sync_session):
                migration_mod.upgrade()

        with pytest.raises(RuntimeError) as excinfo:
            await async_session.run_sync(_run_upgrade)

        message = str(excinfo.value)
        assert "('magnuscarlsen', 'en', 2)" in message
        assert "Resolve them manually" in message
    finally:
        await async_session.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_case_insensitive_functional_index_definitions(async_session):
    result = await async_session.execute(
        text(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = current_schema()
              AND indexname IN (
                  'ix_games_white_player_lower',
                  'ix_games_black_player_lower',
                  'uq_player_reports_player_lang_lower'
              )
            """
        )
    )
    definitions = {
        name: " ".join(indexdef.lower().split()) for name, indexdef in result.all()
    }

    assert set(definitions) == {
        "ix_games_white_player_lower",
        "ix_games_black_player_lower",
        "uq_player_reports_player_lang_lower",
    }
    assert "lower((white_player)::text)" in definitions[
        "ix_games_white_player_lower"
    ]
    assert "lower((black_player)::text)" in definitions[
        "ix_games_black_player_lower"
    ]
    report_index = definitions["uq_player_reports_player_lang_lower"]
    assert report_index.startswith("create unique index")
    assert "(lower((player_name)::text), language)" in report_index
