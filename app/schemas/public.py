from pydantic import BaseModel, ConfigDict


class ServiceLinks(BaseModel):
    docs: str
    openapi: str
    demo: str
    repository: str


class ServiceIndex(BaseModel):
    name: str
    description: str
    version: str
    links: ServiceLinks

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "name": "Chess Lab API",
                    "description": (
                        "Bulk chess game analysis and opponent scouting with "
                        "Stockfish-backed insights."
                    ),
                    "version": "0.1.0",
                    "links": {
                        "docs": "/docs",
                        "openapi": "/openapi.json",
                        "demo": "/api/v1/demo",
                        "repository": "https://github.com/Roman2050/chess-lab",
                    },
                }
            ]
        }
    )


class DemoLinks(BaseModel):
    games: str
    status: str
    stats: str
    openings: str
    moves: str
    report: str


class DemoDiscovery(BaseModel):
    player_name: str
    description: str
    read_only: bool
    report_languages: tuple[str, ...]
    links: DemoLinks

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "player_name": "DemoPlayer",
                    "description": "Read-only demonstration of analyzed games and insights.",
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
            ]
        }
    )


class HealthStatus(BaseModel):
    status: str

    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "ok"}]})


class ReadinessStatus(HealthStatus):
    redis: str

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"status": "ok", "redis": "ok"}]}
    )
