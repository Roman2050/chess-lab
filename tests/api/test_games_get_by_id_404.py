import httpx
import pytest

from app.database import get_async_db


class _FakeResult:
    def scalar_one_or_none(self):
        return None


class _FakeSession:
    async def execute(self, *_args, **_kwargs):
        return _FakeResult()


@pytest.fixture
def override_db(app):
    async def _override():
        yield _FakeSession()

    app.dependency_overrides[get_async_db] = _override
    yield
    app.dependency_overrides.clear()


@pytest.fixture
async def api_client(app, override_db):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_game_by_id_returns_404_when_missing(api_client):
    resp = await api_client.get("/api/v1/games/12345")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Game not found"
