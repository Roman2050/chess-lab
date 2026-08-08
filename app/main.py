from fastapi import FastAPI, Response, status

from app.routers import games, analysis, report
from app.services.rate_limit import is_rate_limit_backend_ready


app = FastAPI(
    title="Chess Lab API",
    description="Chess game analysis and training system",
    version="0.1.0"
)

app.include_router(games.router)
app.include_router(analysis.router)
app.include_router(report.router)

@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/ready", response_model=dict[str, str])
async def readiness_check(response: Response) -> dict[str, str]:
    """Report whether Redis can enforce expensive-operation quotas."""
    if await is_rate_limit_backend_ready():
        return {"status": "ok", "redis": "ok"}

    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "unavailable", "redis": "unavailable"}
