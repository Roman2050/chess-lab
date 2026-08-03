from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.database import get_async_db

PLAYER = "hero"


class _FakeSession:
    """Routes only call the patched repository helpers, so the session is inert."""


@pytest.fixture
def override_db(app):
    async def _override():
        yield _FakeSession()

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


@pytest.fixture
def patched(monkeypatch):
    """Patch the router's collaborators and expose the mocks for assertions.

    The claim and the enqueue are attached to one parent mock so their relative
    order is assertable — ``generating`` must be committed before the task can
    possibly start.
    """
    import app.routers.report as report_router

    count = AsyncMock()
    get = AsyncMock()
    stale = AsyncMock(return_value=False)
    upsert = AsyncMock(return_value=True)
    release = AsyncMock()
    delay = MagicMock()

    calls = MagicMock()
    calls.attach_mock(upsert, "upsert")
    calls.attach_mock(release, "release")
    calls.attach_mock(delay, "delay")

    monkeypatch.setattr(report_router, "count_analyzed_games", count)
    monkeypatch.setattr(report_router, "get_report", get)
    monkeypatch.setattr(report_router, "is_generation_stale", stale)
    monkeypatch.setattr(report_router, "upsert_generating", upsert)
    monkeypatch.setattr(report_router, "release_generating", release)
    monkeypatch.setattr(report_router.generate_player_report, "delay", delay)

    return SimpleNamespace(
        count=count,
        get=get,
        stale=stale,
        upsert=upsert,
        release=release,
        delay=delay,
        calls=calls,
    )


def _make_report(
    *,
    analyzed_games_count: int = 0,
    report_text: str | None = "cached text",
    status: str = "ready",
    last_game_played_at: datetime | None = None,
):
    return SimpleNamespace(
        analyzed_games_count=analyzed_games_count,
        report_text=report_text,
        status=status,
        created_at=datetime(2026, 1, 1, 12, 0, 0),
        updated_at=datetime(2026, 1, 2, 12, 0, 0),
        last_game_played_at=last_game_played_at,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_post_generate_enqueues_and_202(api_client, patched):
    patched.count.return_value = 20
    patched.get.return_value = None

    resp = await api_client.post(f"/report/{PLAYER}")

    assert resp.status_code == 202
    body = resp.json()
    assert body["action"] == "generate"
    patched.delay.assert_called_once_with(PLAYER, "en")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_post_report_sets_generating_before_enqueue(api_client, patched):
    """The status must be committed first, or the worker could outrun the row."""
    patched.count.return_value = 20
    patched.get.return_value = None

    await api_client.post(f"/report/{PLAYER}")

    _db, *claim_args = patched.upsert.await_args.args
    assert claim_args == [PLAYER, "en", 20]
    assert [name for name, *_ in patched.calls.mock_calls] == ["upsert", "delay"]
    patched.release.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_post_lost_claim_race_does_not_enqueue(api_client, patched):
    """Another request claimed the report between our read and our write."""
    patched.count.return_value = 20
    patched.get.return_value = None
    patched.upsert.return_value = False

    resp = await api_client.post(f"/report/{PLAYER}")

    assert resp.status_code == 202
    assert resp.json()["action"] == "already_generating"
    patched.delay.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_post_stale_generation_is_reclaimed(api_client, patched):
    """A `generating` row past its lease belongs to a dead worker — take it over."""
    patched.count.return_value = 50
    patched.get.return_value = _make_report(
        analyzed_games_count=20, status="generating", report_text=None
    )
    patched.stale.return_value = True

    resp = await api_client.post(f"/report/{PLAYER}")

    assert resp.status_code == 202
    assert resp.json()["action"] == "generate"
    patched.delay.assert_called_once_with(PLAYER, "en")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_post_enqueue_failure_releases_the_claim(api_client, patched):
    """A dead broker must not leave the player stuck behind a `generating` row."""
    patched.count.return_value = 20
    patched.get.return_value = None
    patched.delay.side_effect = OSError("redis is down")

    resp = await api_client.post(f"/report/{PLAYER}")

    assert resp.status_code == 503
    assert [name for name, *_ in patched.calls.mock_calls] == [
        "upsert",
        "delay",
        "release",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_post_insufficient_200(api_client, patched):
    patched.count.return_value = 5
    patched.get.return_value = None

    resp = await api_client.post(f"/report/{PLAYER}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "insufficient_games"
    assert body["games_until_next_report"] == 15
    patched.delay.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_post_up_to_date_200(api_client, patched):
    patched.count.return_value = 25
    patched.get.return_value = _make_report(analyzed_games_count=20)

    resp = await api_client.post(f"/report/{PLAYER}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "up_to_date"
    assert body["report_games_count"] == 20
    assert body["games_until_next_report"] == 15  # threshold 20 - delta 5
    patched.delay.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_post_already_generating_202(api_client, patched):
    patched.count.return_value = 50
    patched.get.return_value = _make_report(
        analyzed_games_count=20, status="generating", report_text=None
    )

    resp = await api_client.post(f"/report/{PLAYER}")

    assert resp.status_code == 202
    assert resp.json()["action"] == "already_generating"
    patched.upsert.assert_not_awaited()
    patched.delay.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_report_404_when_none(api_client, patched):
    patched.get.return_value = None
    patched.count.return_value = 0

    resp = await api_client.get(f"/report/{PLAYER}")

    assert resp.status_code == 404


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_report_returns_text_and_is_stale(api_client, patched):
    patched.get.return_value = _make_report(
        analyzed_games_count=10, report_text="here is your report"
    )
    patched.count.return_value = 40  # delta 30 >= 20 → stale

    resp = await api_client.get(f"/report/{PLAYER}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["report_text"] == "here is your report"
    assert body["analyzed_games_count"] == 10
    assert body["current_analyzed_games_count"] == 40
    assert body["is_stale"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_status_reports_state(api_client, patched):
    patched.get.return_value = _make_report(
        analyzed_games_count=20, report_text="text", status="ready"
    )
    patched.count.return_value = 25

    resp = await api_client.get(f"/report/{PLAYER}/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["has_report"] is True
    assert body["analyzed_games_count"] == 20
    assert body["current_analyzed_games_count"] == 25
    assert body["games_until_next_report"] == 15
