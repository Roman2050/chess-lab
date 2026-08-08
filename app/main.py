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
APP_DESCRIPTION = "Chess game analysis and opponent scouting backend"
APP_VERSION = "0.1.0"
REPOSITORY_URL = "https://github.com/Roman2050/chess-lab"

app = FastAPI(
    title=APP_NAME,
    description=APP_DESCRIPTION,
    version=APP_VERSION,
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


@app.get("/", response_model=ServiceIndex, tags=["Service"])
def service_index() -> ServiceIndex:
    """Return public service metadata and discovery links without I/O."""
    return ServiceIndex(
        name=APP_NAME,
        description=APP_DESCRIPTION,
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


@app.get("/health", response_model=HealthStatus, tags=["Infrastructure"])
def health_check() -> HealthStatus:
    return HealthStatus(status="ok")


@app.get("/ready", response_model=ReadinessStatus, tags=["Infrastructure"])
async def readiness_check(response: Response) -> ReadinessStatus:
    """Report whether Redis can enforce expensive-operation quotas."""
    if await is_rate_limit_backend_ready():
        return ReadinessStatus(status="ok", redis="ok")

    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessStatus(status="unavailable", redis="unavailable")
