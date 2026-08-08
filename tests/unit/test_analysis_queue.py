from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy.dialects import postgresql

from app.database import get_async_db
from app.services.analysis_queue import get_analysis_progress, get_unanalyzed_game_ids

PLAYER = "hero"


# ═══════════════════════════════════════════════════════════════════
# API fixtures — DB dependency stubbed out, services patched per test.
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def override_db(app):
    async def _override():
        yield object()

    app.dependency_overrides[get_async_db] = _override
    yield
    app.dependency_overrides.clear()


@pytest.fixture
async def api_client(app, override_db, auth_headers):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=auth_headers,
    ) as client:
        yield client


# ═══════════════════════════════════════════════════════════════════
# POST /api/v1/analyze/player/{username} — fan-out
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.unit
@pytest.mark.asyncio
async def test_enqueue_player_analysis_respects_task_limit(api_client, monkeypatch):
    """A batch request never publishes more than its configured task budget."""
    import app.routers.analysis as analysis_router

    async def fake_ids(_db, player_name, *, limit):
        assert player_name == PLAYER
        assert limit == 10
        return list(range(1, 26))

    delay_mock = MagicMock()
    monkeypatch.setattr(
        analysis_router.settings,
        "MAX_ANALYSIS_TASKS_PER_REQUEST",
        10,
    )
    monkeypatch.setattr(analysis_router, "get_unanalyzed_game_ids", fake_ids)
    monkeypatch.setattr(analysis_router.analyze_game, "delay", delay_mock)

    resp = await api_client.post(f"/api/v1/analyze/player/{PLAYER}")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"status": "queued", "player": PLAYER, "queued_count": 10}

    assert delay_mock.call_count == 10
    assert [call.args[0] for call in delay_mock.call_args_list] == list(range(1, 11))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_enqueue_player_analysis_empty_404(api_client, monkeypatch):
    """No unanalyzed games → 404 and nothing is enqueued."""
    import app.routers.analysis as analysis_router

    async def fake_ids(_db, _player_name, *, limit):
        assert limit == 10
        return []

    delay_mock = MagicMock()
    monkeypatch.setattr(analysis_router, "get_unanalyzed_game_ids", fake_ids)
    monkeypatch.setattr(analysis_router.analyze_game, "delay", delay_mock)

    resp = await api_client.post(f"/api/v1/analyze/player/{PLAYER}")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "No unanalyzed games for player"
    delay_mock.assert_not_called()


# ═══════════════════════════════════════════════════════════════════
# GET /api/v1/analyze/player/{username}/status — progress
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.unit
@pytest.mark.asyncio
async def test_analysis_status_counts(api_client, monkeypatch):
    """Progress dict from the service is surfaced verbatim under the player."""
    import app.routers.analysis as analysis_router

    async def fake_progress(_db, player_name):
        assert player_name == PLAYER
        return {"total": 10, "analyzed": 4, "pending": 6}

    monkeypatch.setattr(analysis_router, "get_analysis_progress", fake_progress)

    resp = await api_client.get(f"/api/v1/analyze/player/{PLAYER}/status")

    assert resp.status_code == 200
    assert resp.json() == {
        "player": PLAYER,
        "total": 10,
        "analyzed": 4,
        "pending": 6,
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_analysis_status_unknown_player_404(api_client, monkeypatch):
    """total == 0 means the player has no games at all → 404."""
    import app.routers.analysis as analysis_router

    async def fake_progress(_db, _player_name):
        return {"total": 0, "analyzed": 0, "pending": 0}

    monkeypatch.setattr(analysis_router, "get_analysis_progress", fake_progress)

    resp = await api_client.get(f"/api/v1/analyze/player/{PLAYER}/status")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Player not found"


# ═══════════════════════════════════════════════════════════════════
# get_unanalyzed_game_ids — which rows the fan-out picks up
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unanalyzed_ids_selects_claimable_statuses_only():
    """Games already `running` are skipped — only pending/failed are re-enqueued."""
    captured: list = []

    class _FakeDb:
        async def execute(self, stmt):
            captured.append(stmt)
            result = MagicMock()
            result.scalars.return_value.all.return_value = [7]
            return result

    ids = await get_unanalyzed_game_ids(_FakeDb(), "HeRo", limit=10)

    assert ids == [7]
    sql = str(
        captured[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "games.analysis_status IN ('pending', 'failed')" in sql
    assert "is_analyzed" not in sql
    assert "lower(games.white_player) = lower('HeRo')" in sql
    assert "lower(games.black_player) = lower('HeRo')" in sql
    assert "ORDER BY games.id" in sql
    assert "LIMIT 10" in sql


@pytest.mark.unit
@pytest.mark.asyncio
async def test_progress_query_is_case_insensitive():
    captured: list = []

    class _FakeDb:
        async def execute(self, stmt):
            captured.append(stmt)
            result = MagicMock()
            result.one.return_value = SimpleNamespace(total=0, analyzed=0)
            return result

    progress = await get_analysis_progress(_FakeDb(), "HeRo")

    assert progress == {"total": 0, "analyzed": 0, "pending": 0}
    sql = str(
        captured[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "lower(games.white_player) = lower('HeRo')" in sql
    assert "lower(games.black_player) = lower('HeRo')" in sql
