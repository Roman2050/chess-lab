import tomllib
from pathlib import Path

import pytest

from app.config import API_V1_PREFIX, settings
from app.main import (
    APP_DESCRIPTION,
    APP_LICENSE_IDENTIFIER,
    APP_LICENSE_NAME,
    APP_NAME,
    APP_SUMMARY,
    APP_VERSION,
    CONTACT_URL,
    OPENAPI_TAGS,
    REPOSITORY_URL,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_service_index_is_public_stable_and_secret_free(async_client) -> None:
    response = await async_client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "name": "Chess Lab API",
        "description": APP_SUMMARY,
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
async def test_openapi_metadata_is_ordered_real_and_secret_free(async_client) -> None:
    response = await async_client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["info"] == {
        "title": APP_NAME,
        "summary": APP_SUMMARY,
        "description": APP_DESCRIPTION,
        "contact": {"name": "Roman", "url": CONTACT_URL},
        "license": {
            "name": APP_LICENSE_NAME,
            "identifier": APP_LICENSE_IDENTIFIER,
        },
        "version": APP_VERSION,
    }
    assert schema["tags"] == OPENAPI_TAGS

    serialized = response.text
    assert "Add your description here" not in serialized
    assert "example.invalid" not in serialized
    assert settings.MVP_API_KEY.get_secret_value() not in serialized
    assert settings.LICHESS_API_TOKEN is None or (
        settings.LICHESS_API_TOKEN.get_secret_value() not in serialized
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openapi_operations_are_documented_and_grouped(async_client) -> None:
    schema = (await async_client.get("/openapi.json")).json()
    expected_tags = {
        "Demo",
        "Games",
        "Player Statistics",
        "Analysis",
        "Reports",
        "Operator Imports",
        "Service Health",
    }

    actual_tags = set()
    for _path, path_item in schema["paths"].items():
        for method in ("get", "post"):
            operation = path_item.get(method)
            if operation is None:
                continue
            actual_tags.update(operation["tags"])
            assert operation["summary"]
            assert operation["description"]
            assert operation["responses"]["200"]["description"]

    assert actual_tags == expected_tags
    assert schema["paths"]["/api/v1/report/{username}"]["post"]["responses"]["202"]["description"]
    assert schema["paths"]["/api/v1/games/lichess/{username}"]["post"]["responses"]["409"][
        "description"
    ]
    assert schema["paths"]["/api/v1/games/lichess/{username}"]["post"]["responses"]["429"][
        "description"
    ].startswith("Operation quota exhausted")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openapi_response_schemas_have_safe_examples(async_client) -> None:
    schemas = (await async_client.get("/openapi.json")).json()["components"]["schemas"]

    for name in (
        "AnalysisProgress",
        "BatchAnalysisResponse",
        "DemoDiscovery",
        "GameDetail",
        "GameSummary",
        "HealthStatus",
        "MoveAccuracyStat",
        "OpeningStat",
        "PaginatedGames",
        "PlayerStats",
        "ReadinessStatus",
        "ReportRequestResponse",
        "ReportResponse",
        "ReportStatusResponse",
        "ServiceIndex",
        "UploadResponse",
    ):
        assert schemas[name]["examples"]

    serialized_examples = str({name: schema.get("examples") for name, schema in schemas.items()})
    assert settings.MVP_API_KEY.get_secret_value() not in serialized_examples
    assert "example.invalid" not in serialized_examples


@pytest.mark.unit
def test_package_readme_and_openapi_metadata_are_aligned() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    project = pyproject["project"]

    assert project["description"] == APP_SUMMARY
    assert project["version"] == APP_VERSION
    assert project["license"] == APP_LICENSE_IDENTIFIER
    assert project["license-files"] == ["LICENSE", "THIRD_PARTY_NOTICES.md"]
    assert project["urls"] == {
        "Homepage": REPOSITORY_URL,
        "Repository": REPOSITORY_URL,
        "Documentation": f"{REPOSITORY_URL}#readme",
        "Architecture": f"{REPOSITORY_URL}/blob/main/ARCHITECTURE.md",
    }
    assert APP_SUMMARY in readme
    assert f"**{APP_VERSION}**" in readme
    assert REPOSITORY_URL in readme
    assert CONTACT_URL in readme
    assert APP_LICENSE_IDENTIFIER in readme
    assert "THIRD_PARTY_NOTICES.md" in readme


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cors_allows_only_configured_origin(async_client) -> None:
    allowed = await async_client.get("/api/v1/demo", headers={"Origin": "https://frontend.example"})
    unknown = await async_client.get("/api/v1/demo", headers={"Origin": "https://unknown.example"})

    assert allowed.headers["access-control-allow-origin"] == ("https://frontend.example")
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
        assert response.headers["access-control-allow-origin"] == ("https://frontend.example")
        assert method in response.headers["access-control-allow-methods"]
        assert "x-api-key" not in response.headers["access-control-allow-headers"].lower()
