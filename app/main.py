from importlib.metadata import version
from urllib.parse import quote

from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware

from app.config import API_V1_PREFIX, settings
from app.routers import analysis, games, report
from app.schemas.public import (
    DemoDiscovery,
    DemoLinks,
    HealthStatus,
    ReadinessStatus,
    ServiceIndex,
    ServiceLinks,
)
from app.services.rate_limit import is_rate_limit_backend_ready


APP_NAME = "Chess Lab API"
APP_SUMMARY = "Bulk chess game analysis and opponent scouting with Stockfish-backed insights."
APP_VERSION = version("chess-lab")
REPOSITORY_URL = "https://github.com/Roman2050/chess-lab"
CONTACT_URL = "https://github.com/Roman2050"

APP_DESCRIPTION = f"""
Chess Lab turns a player's game history into a focused opponent-scouting report.

### How it works

1. An operator imports a bounded Lichess export or uploads a UTF-8 PGN.
2. Celery workers run Stockfish analysis asynchronously with `MultiPV=2`.
3. Deterministic code aggregates move quality, phases, openings, and error patterns.
4. An LLM narrates those computed facts; it does not calculate or invent chess metrics.

### Start with the public demo

Open [`GET /api/v1/demo`](/api/v1/demo) to discover the configured demo player and
links to public games, statistics, analysis progress, and the cached scouting report.
Public read endpoints require no API key.

All `POST` endpoints are **operator-only** and require `X-API-Key`. The key belongs to
one server-side operator and is never issued to demo users or embedded in a browser.
Background report generation returns `202`; poll the corresponding status endpoint,
then read the cached result. A `409` means a deployment-wide Lichess import is already
active. A `429` includes `Retry-After`; wait that many seconds before retrying.

See the [repository]({REPOSITORY_URL}) and
[architecture]({REPOSITORY_URL}/blob/main/ARCHITECTURE.md) for implementation details.
"""

OPENAPI_TAGS = [
    {
        "name": "Demo",
        "description": "Public entry points for service metadata and the read-only demo flow.",
    },
    {
        "name": "Games",
        "description": "Public read access to imported games and individual game details.",
    },
    {
        "name": "Player Statistics",
        "description": "Public deterministic statistics derived from completed Stockfish analysis.",
    },
    {
        "name": "Analysis",
        "description": "Operator-only analysis requests and public database-backed progress polling.",
    },
    {
        "name": "Reports",
        "description": "Operator-only generation and public reads of cached scouting reports.",
    },
    {
        "name": "Operator Imports",
        "description": "Operator-only, quota-protected Lichess and PGN ingestion.",
    },
    {
        "name": "Service Health",
        "description": "Public liveness and Redis-backed readiness probes.",
    },
]

app = FastAPI(
    title=APP_NAME,
    summary=APP_SUMMARY,
    description=APP_DESCRIPTION,
    version=APP_VERSION,
    contact={"name": "Roman", "url": CONTACT_URL},
    openapi_tags=OPENAPI_TAGS,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.CORS_ALLOWED_ORIGINS),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Accept", "Content-Type"],
)

app.include_router(games.router, prefix=API_V1_PREFIX)
app.include_router(analysis.router, prefix=API_V1_PREFIX)
app.include_router(report.router, prefix=API_V1_PREFIX)


@app.get(
    "/",
    response_model=ServiceIndex,
    tags=["Demo"],
    summary="Discover Chess Lab",
    description=(
        "**Public read.** Return stable service metadata and links without database "
        "or external-service calls."
    ),
    response_description="Service metadata and discovery links.",
)
def service_index() -> ServiceIndex:
    """Return public service metadata and discovery links without I/O."""
    return ServiceIndex(
        name=APP_NAME,
        description=APP_SUMMARY,
        version=APP_VERSION,
        links=ServiceLinks(
            docs="/docs",
            openapi="/openapi.json",
            demo=f"{API_V1_PREFIX}/demo",
            repository=REPOSITORY_URL,
        ),
    )


@app.get(
    f"{API_V1_PREFIX}/demo",
    response_model=DemoDiscovery,
    tags=["Demo"],
    summary="Discover the public demo",
    description=(
        "**Public read.** Return the configured pseudonymized demo player and stable "
        "links for the complete read-only portfolio flow. The endpoint performs no "
        "database lookups and does not validate every link."
    ),
    response_description="Demo identity, supported report languages, and read-only links.",
)
def demo_discovery() -> DemoDiscovery:
    """Return stable read-only links for the configured public demo player."""
    player_name = settings.DEMO_PLAYER_NAME
    player_path = quote(player_name, safe="")
    return DemoDiscovery(
        player_name=player_name,
        description=(
            "Read-only demonstration of analyzed games, aggregate statistics, "
            "analysis progress, and a cached scouting report."
        ),
        read_only=True,
        report_languages=settings.REPORT_ALLOWED_LANGUAGES,
        links=DemoLinks(
            games=f"{API_V1_PREFIX}/games?player_name={player_path}",
            status=f"{API_V1_PREFIX}/analyze/player/{player_path}/status",
            stats=f"{API_V1_PREFIX}/games/stats/{player_path}",
            openings=f"{API_V1_PREFIX}/games/stats/{player_path}/openings",
            moves=f"{API_V1_PREFIX}/games/stats/{player_path}/moves",
            report=f"{API_V1_PREFIX}/report/{player_path}",
        ),
    )


@app.get(
    "/health",
    response_model=HealthStatus,
    tags=["Service Health"],
    summary="Check process liveness",
    description=(
        "**Public read.** Confirm that the API process is alive. This probe does not "
        "call PostgreSQL, Redis, Lichess, or the LLM provider."
    ),
    response_description="API process liveness status.",
)
def health_check() -> HealthStatus:
    return HealthStatus(status="ok")


@app.get(
    "/ready",
    response_model=ReadinessStatus,
    tags=["Service Health"],
    summary="Check quota-backend readiness",
    description=(
        "**Public read.** Check whether Redis is available to enforce operation "
        "quotas. Returns `503` when protected writes must fail closed."
    ),
    response_description="API readiness and Redis availability.",
    responses={503: {"description": "Redis is unavailable; operator writes are disabled."}},
)
async def readiness_check(response: Response) -> ReadinessStatus:
    """Report whether Redis can enforce expensive-operation quotas."""
    if await is_rate_limit_backend_ready():
        return ReadinessStatus(status="ok", redis="ok")

    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessStatus(status="unavailable", redis="unavailable")
