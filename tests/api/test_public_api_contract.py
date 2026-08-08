import pytest

from app.config import API_V1_PREFIX, settings


@pytest.mark.unit
@pytest.mark.asyncio
async def test_service_index_is_public_stable_and_secret_free(async_client) -> None:
    response = await async_client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "name": "Chess Lab API",
        "description": "Chess game analysis and opponent scouting backend",
        "version": "0.1.0",
        "links": {
            "docs": "/docs",
            "openapi": "/openapi.json",
            "demo": "/api/v1/demo",
            "repository": "https://github.com/Roman2050/chess-lab",
        },
    }
    serialized = response.text
    assert settings.MVP_API_KEY.get_secret_value() not in serialized
    assert settings.DB_HOST not in serialized
    assert "redis://" not in serialized


@pytest.mark.unit
@pytest.mark.asyncio
async def test_demo_discovery_builds_versioned_read_only_links(async_client) -> None:
    response = await async_client.get(f"{API_V1_PREFIX}/demo")

    assert response.status_code == 200
    assert response.json() == {
        "player_name": "DemoPlayer",
        "description": (
            "Read-only demonstration of analyzed games, aggregate statistics, "
            "analysis progress, and a cached scouting report."
        ),
        "read_only": True,
        "report_languages": ["en", "uk"],
        "links": {
            "games": "/api/v1/games?player_name=DemoPlayer",
            "status": "/api/v1/analyze/player/DemoPlayer/status",
            "stats": "/api/v1/games/stats/DemoPlayer",
            "openings": "/api/v1/games/stats/DemoPlayer/openings",
            "moves": "/api/v1/games/stats/DemoPlayer/moves",
            "report": "/api/v1/report/DemoPlayer",
        },
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_only_business_routes_are_versioned(async_client) -> None:
    openapi = (await async_client.get("/openapi.json")).json()
    paths = set(openapi["paths"])

    assert "/api/v1/games" in paths
    assert "/api/v1/analyze/player/{username}/status" in paths
    assert "/api/v1/report/{username}" in paths
    assert "/games" not in paths
    assert "/analyze/player/{username}/status" not in paths
    assert "/report/{username}" not in paths
    assert (await async_client.get("/games")).status_code == 404

    for path in ("/", "/health", "/ready", "/docs", "/openapi.json"):
        assert path in paths or (await async_client.get(path)).status_code == 200


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cors_allows_only_configured_origin(async_client) -> None:
    allowed = await async_client.get(
        "/api/v1/demo", headers={"Origin": "https://frontend.example"}
    )
    unknown = await async_client.get(
        "/api/v1/demo", headers={"Origin": "https://unknown.example"}
    )

    assert allowed.headers["access-control-allow-origin"] == (
        "https://frontend.example"
    )
    assert "access-control-allow-origin" not in unknown.headers
    assert settings.MVP_API_KEY.get_secret_value() not in str(allowed.headers)
    assert settings.MVP_API_KEY.get_secret_value() not in allowed.text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cors_preflight_allows_get_post_and_content_type(async_client) -> None:
    for method in ("GET", "POST"):
        response = await async_client.options(
            "/api/v1/demo",
            headers={
                "Origin": "https://frontend.example",
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "Content-Type",
            },
        )

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == (
            "https://frontend.example"
        )
        assert method in response.headers["access-control-allow-methods"]
        assert "x-api-key" not in response.headers[
            "access-control-allow-headers"
        ].lower()
