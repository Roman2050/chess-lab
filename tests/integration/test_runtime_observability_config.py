from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.integration
def test_production_compose_has_bounded_log_retention() -> None:
    compose = (PROJECT_ROOT / "compose.production.yaml").read_text(encoding="utf-8")

    assert "x-bounded-logging: &bounded-logging" in compose
    assert "driver: local" in compose
    assert 'max-size: "10m"' in compose
    assert 'max-file: "5"' in compose
    assert "logging: *bounded-logging" in compose
    assert compose.count("logging: *bounded-logging") == 4
    assert "--no-access-log" in compose
    assert "log_statement=none" in compose
    assert "log_statement=all" not in compose


@pytest.mark.integration
def test_caddy_access_logs_drop_credentials_queries_and_health_noise() -> None:
    caddyfile = (PROJECT_ROOT / "Caddyfile").read_text(encoding="utf-8")

    assert "output stdout" in caddyfile
    assert "request>headers delete" in caddyfile
    assert "request>uri delete" in caddyfile
    assert "log_append <path {http.request.uri.path}" in caddyfile
    assert "log_append <request_size {http.request.header.Content-Length}" in caddyfile
    assert "log_skip @routine_health" in caddyfile
    assert "http.request.body" not in caddyfile
    assert "http.response.body" not in caddyfile


@pytest.mark.integration
def test_production_environment_enables_json_logging() -> None:
    environment = (PROJECT_ROOT / ".env.production.example").read_text(encoding="utf-8")

    assert "APP_ENVIRONMENT=production" in environment
    assert "LOG_LEVEL=INFO" in environment
    assert "APP_VERSION=0.1.0" in environment
