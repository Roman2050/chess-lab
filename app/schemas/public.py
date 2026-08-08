from pydantic import BaseModel


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


class HealthStatus(BaseModel):
    status: str


class ReadinessStatus(HealthStatus):
    redis: str
