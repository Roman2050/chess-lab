import httpx
import pytest

from app.database import get_async_db


class _FakeSession:
    async def execute(self, *_args, **_kwargs):  # pragma: no cover - never hit
        raise AssertionError("aggregation is mocked; DB should not be queried")


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
async def test_moves_endpoint_exposes_avg_wp_loss(api_client, monkeypatch) -> None:
    """The /moves endpoint surfaces avg_wp_loss alongside avg_cp_loss (Phase 6)."""

    async def _fake_rows(db, player_name, min_games):
        return [
            {
                "move_num": 5,
                "games_count": 10,
                "avg_cp_loss": 42.0,
                "avg_wp_loss": 3.14,
                "inaccuracy_rate": 10.0,
                "mistake_rate": 5.0,
                "blunder_rate": 1.0,
            }
        ]

    monkeypatch.setattr(
        "app.routers.games.get_accuracy_by_move_number", _fake_rows
    )

    resp = await api_client.get("/api/v1/games/stats/hero/moves")

    assert resp.status_code == 200
    row = resp.json()[0]
    assert row["avg_cp_loss"] == 42.0
    assert row["avg_wp_loss"] == 3.14
