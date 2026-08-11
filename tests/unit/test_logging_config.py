import json
import logging
import sys

import pytest

from app.logging_config import JsonLogFormatter, log_context


def _formatter() -> JsonLogFormatter:
    return JsonLogFormatter(
        service="worker-analysis",
        environment="production",
        app_version="0.1.0",
    )


@pytest.mark.unit
def test_json_formatter_serializes_base_and_lifecycle_fields() -> None:
    record = logging.LogRecord(
        name="app.services.lichess",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="lichess.request.succeeded",
        args=(),
        exc_info=None,
    )
    record.operation_id = "operation-123"
    record.status = "succeeded"
    record.duration_ms = 125.5
    record.upstream_http_status = 200
    record.factual_body_bytes = 4096

    payload = json.loads(_formatter().format(record))

    assert payload == {
        "timestamp": payload["timestamp"],
        "level": "INFO",
        "logger": "app.services.lichess",
        "event": "lichess.request.succeeded",
        "service": "worker-analysis",
        "environment": "production",
        "version": "0.1.0",
        "operation_id": "operation-123",
        "status": "succeeded",
        "duration_ms": 125.5,
        "upstream_http_status": 200,
        "factual_body_bytes": 4096,
    }
    assert payload["timestamp"].endswith("Z")


@pytest.mark.unit
def test_json_formatter_adds_bound_task_context() -> None:
    record = logging.LogRecord(
        name="app.tasks.celery_app",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="analysis.task.started",
        args=(),
        exc_info=None,
    )

    with log_context(task_id="task-123", game_id=42):
        payload = json.loads(_formatter().format(record))

    assert payload["task_id"] == "task-123"
    assert payload["game_id"] == 42


@pytest.mark.unit
def test_exception_output_excludes_secrets_payloads_and_original_text(monkeypatch) -> None:
    api_key = "operator-api-key-that-must-not-appear"
    pgn = '[Event "Private"] 1. e4 e5'
    prompt = "private report prompt and generated report"
    monkeypatch.setenv("MVP_API_KEY", api_key)
    formatter = _formatter()

    try:
        raise RuntimeError(f"provider failed: {api_key}; {pgn}; {prompt}")
    except RuntimeError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="app.tasks.celery_app",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="report.task.failed",
        args=(),
        exc_info=exc_info,
    )
    # Arbitrary extras are not part of the serializer allowlist.
    record.report_prompt = prompt
    record.pgn_content = pgn

    rendered = formatter.format(record)
    payload = json.loads(rendered)

    assert api_key not in rendered
    assert pgn not in rendered
    assert prompt not in rendered
    assert payload["exception"]["type"] == "RuntimeError"
    assert payload["exception"]["traceback"]
    assert "message" not in payload["exception"]


@pytest.mark.unit
def test_known_secret_in_event_is_redacted(monkeypatch) -> None:
    secret = "database-password-secret"
    monkeypatch.setenv("DB_PASSWORD", secret)
    formatter = _formatter()
    record = logging.LogRecord(
        name="third.party",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=f"connection failed for password={secret}",
        args=(),
        exc_info=None,
    )

    rendered = formatter.format(record)

    assert secret not in rendered
    assert "[REDACTED]" in rendered
