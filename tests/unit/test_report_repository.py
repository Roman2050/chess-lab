"""Unit tests for case-insensitive report persistence and claiming.

No DB: repository correctness is readable from the compiled SQL statements.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.config import settings
from app.services.report_repository import (
    count_analyzed_games,
    generating_claim_stmt,
    get_report,
    get_report_sync,
    release_generating,
)

PLAYER = "hero"
LANGUAGE = "en"


def _compiled(stmt):
    return stmt.compile(dialect=postgresql.dialect())


def _literal_sql(stmt) -> str:
    return str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


class _AsyncDb:
    def __init__(self, rowcounts: list[int] | None = None) -> None:
        self.executed: list = []
        self.rowcounts = rowcounts or []
        self.commits = 0

    async def execute(self, stmt):
        self.executed.append(stmt)
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        result.scalar_one.return_value = 0
        index = len(self.executed) - 1
        result.rowcount = self.rowcounts[index] if index < len(self.rowcounts) else 1
        return result

    async def commit(self) -> None:
        self.commits += 1


class _SyncDb:
    def __init__(self) -> None:
        self.executed: list = []

    def execute(self, stmt):
        self.executed.append(stmt)
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        return result


@pytest.mark.unit
async def test_report_lookup_case_insensitive_async() -> None:
    db = _AsyncDb()

    assert await get_report(db, "HeRo", LANGUAGE) is None

    sql = _literal_sql(db.executed[0])
    assert "lower(player_reports.player_name) = lower('HeRo')" in sql
    assert "player_reports.language = 'en'" in sql


@pytest.mark.unit
def test_report_lookup_case_insensitive_sync() -> None:
    db = _SyncDb()

    assert get_report_sync(db, "HeRo", LANGUAGE) is None

    sql = _literal_sql(db.executed[0])
    assert "lower(player_reports.player_name) = lower('HeRo')" in sql
    assert "player_reports.language = 'en'" in sql


@pytest.mark.unit
async def test_analyzed_game_count_is_case_insensitive() -> None:
    db = _AsyncDb()

    assert await count_analyzed_games(db, "HeRo") == 0

    sql = _literal_sql(db.executed[0])
    assert "lower(games.white_player) = lower('HeRo')" in sql
    assert "lower(games.black_player) = lower('HeRo')" in sql


@pytest.mark.unit
async def test_release_predicates_case_insensitive() -> None:
    placeholder_db = _AsyncDb(rowcounts=[1])
    await release_generating(placeholder_db, "HeRo", LANGUAGE)

    delete_sql = _literal_sql(placeholder_db.executed[0])
    assert "lower(player_reports.player_name) = lower('HeRo')" in delete_sql

    previous_report_db = _AsyncDb(rowcounts=[0, 1])
    await release_generating(previous_report_db, "HeRo", LANGUAGE)

    update_sql = _literal_sql(previous_report_db.executed[1])
    assert "lower(player_reports.player_name) = lower('HeRo')" in update_sql


@pytest.mark.unit
def test_claim_uses_lower_player_language_conflict_target():
    sql = str(_compiled(generating_claim_stmt(PLAYER, LANGUAGE, 20)))

    assert "ON CONFLICT (lower(player_name), language) DO UPDATE" in sql
    assert "RETURNING player_reports.id" in sql


@pytest.mark.unit
def test_claim_does_not_overwrite_the_snapshot_count():
    """The existing count still describes the text we have not replaced yet."""
    compiled = _compiled(generating_claim_stmt(PLAYER, LANGUAGE, 20))
    update_clause = str(compiled).split("DO UPDATE")[1].split("WHERE")[0]

    assert "analyzed_games_count" not in update_clause
    assert "player_name" not in update_clause
    assert "status" in update_clause
    # A core INSERT bypasses the ORM `onupdate`, and the lease reads this column.
    assert "updated_at" in update_clause
    assert compiled.params["analyzed_games_count"] == 20


@pytest.mark.unit
def test_claim_takes_over_only_expired_generations():
    compiled = _compiled(generating_claim_stmt(PLAYER, LANGUAGE, 20))
    predicate = str(compiled).split("DO UPDATE")[1].split("WHERE")[1]

    assert "player_reports.status !=" in predicate
    assert "player_reports.updated_at < now()" in predicate
    lease = timedelta(seconds=settings.REPORT_GENERATION_LEASE_SECONDS)
    assert lease in compiled.params.values()
