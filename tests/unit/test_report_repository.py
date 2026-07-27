"""Unit tests for the report claim statement.

No DB: the claim is a single statement, so its correctness is readable off the
compiled SQL — which conflict target it uses, what it does and does not
overwrite, and under which conditions it takes an existing row over.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.dialects import postgresql

from app.config import settings
from app.services.report_repository import generating_claim_stmt

PLAYER = "hero"
LANGUAGE = "en"


def _compiled(stmt):
    return stmt.compile(dialect=postgresql.dialect())


@pytest.mark.unit
def test_claim_upserts_on_the_player_language_constraint():
    sql = str(_compiled(generating_claim_stmt(PLAYER, LANGUAGE, 20)))

    assert (
        "ON CONFLICT ON CONSTRAINT uq_player_reports_player_lang DO UPDATE" in sql
    )
    assert "RETURNING player_reports.id" in sql


@pytest.mark.unit
def test_claim_does_not_overwrite_the_snapshot_count():
    """The existing count still describes the text we have not replaced yet."""
    compiled = _compiled(generating_claim_stmt(PLAYER, LANGUAGE, 20))
    update_clause = str(compiled).split("DO UPDATE")[1].split("WHERE")[0]

    assert "analyzed_games_count" not in update_clause
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
