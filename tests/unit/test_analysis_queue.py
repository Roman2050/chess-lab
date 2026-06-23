from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from app.database import get_async_db

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
async def api_client(app, override_db):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ═══════════════════════════════════════════════════════════════════
# POST /analyze/player/{username} — fan-out
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.unit
@pytest.mark.asyncio
async def test_enqueue_player_analysis_fans_out(api_client, monkeypatch):
    """One small task per unanalyzed game: delay() fires once per id."""
    import app.routers.analysis as analysis_router

    async def fake_ids(_db, player_name):
        assert player_name == PLAYER
        return [1, 2, 3]

    delay_mock = MagicMock()
    monkeypatch.setattr(analysis_router, "get_unanalyzed_game_ids", fake_ids)
    monkeypatch.setattr(analysis_router.analyze_game, "delay", delay_mock)

    resp = await api_client.post(f"/analyze/player/{PLAYER}")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"status": "queued", "player": PLAYER, "queued_count": 3}

    assert delay_mock.call_count == 3
    assert [call.args[0] for call in delay_mock.call_args_list] == [1, 2, 3]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_enqueue_player_analysis_empty_404(api_client, monkeypatch):
    """No unanalyzed games → 404 and nothing is enqueued."""
    import app.routers.analysis as analysis_router

    async def fake_ids(_db, _player_name):
        return []

    delay_mock = MagicMock()
    monkeypatch.setattr(analysis_router, "get_unanalyzed_game_ids", fake_ids)
    monkeypatch.setattr(analysis_router.analyze_game, "delay", delay_mock)

    resp = await api_client.post(f"/analyze/player/{PLAYER}")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "No unanalyzed games for player"
    delay_mock.assert_not_called()


# ═══════════════════════════════════════════════════════════════════
# GET /analyze/player/{username}/status — progress
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

    resp = await api_client.get(f"/analyze/player/{PLAYER}/status")

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

    resp = await api_client.get(f"/analyze/player/{PLAYER}/status")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Player not found"


# ═══════════════════════════════════════════════════════════════════
# analyze_game idempotency guard
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.unit
def test_analyze_game_skips_already_analyzed(monkeypatch):
    """An already-analyzed game must never spin up Stockfish again."""
    import app.tasks.celery_app as celery_app

    monkeypatch.setattr(celery_app.settings, "STOCKFISH_PATH", "/usr/bin/stockfish")

    already_analyzed = SimpleNamespace(id=42, is_analyzed=True)

    fake_session = MagicMock()
    fake_session.get.return_value = already_analyzed

    @contextmanager
    def fake_sync_session():
        yield fake_session

    monkeypatch.setattr(celery_app, "get_sync_db_session", fake_sync_session)

    engine_cls = MagicMock()
    monkeypatch.setattr(celery_app, "StockfishEngine", engine_cls)

    result = celery_app.analyze_game(42)

    assert result is None
    engine_cls.assert_not_called()
