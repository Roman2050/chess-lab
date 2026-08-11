from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

_MAX_EVENT_LENGTH = 2048
_MAX_FIELD_LENGTH = 256
_MAX_TRACEBACK_FRAMES = 20

_LOG_CONTEXT: ContextVar[dict[str, object] | None] = ContextVar(
    "chess_lab_log_context", default=None
)

# Only explicitly approved structured fields are serialized. This keeps arbitrary
# object reprs, request bodies, prompts, PGN, and provider payloads out of logs.
STRUCTURED_LOG_FIELDS = (
    "operation_id",
    "task_id",
    "game_id",
    "status",
    "duration_ms",
    "failure_kind",
    "method",
    "path",
    "http_status",
    "username_normalized",
    "player_name_normalized",
    "language",
    "max_games",
    "perf_type",
    "upstream_http_status",
    "retry_after",
    "rate_limit_source",
    "normalized_content_type",
    "declared_body_bytes",
    "factual_body_bytes",
    "operation",
    "limit",
    "remaining",
    "queued_count",
    "moves_count",
    "analyzed_games_count",
    "retry_number",
)

_configured = False


def _package_version() -> str:
    try:
        return version("chess-lab")
    except PackageNotFoundError:
        return "unknown"


def _known_secret_values() -> tuple[str, ...]:
    names = ("MVP_API_KEY", "LICHESS_API_TOKEN", "DB_PASSWORD", "LLM_API_KEY")
    return tuple(
        value for name in names if (value := os.getenv(name, "").strip()) and len(value) >= 8
    )


def _bounded_text(value: object, limit: int = _MAX_FIELD_LENGTH) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...[truncated]"


def _safe_value(value: object) -> str | int | float | bool | None:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _bounded_text(value)


class _SafeFormatter(logging.Formatter):
    def __init__(self, *, service: str, environment: str, app_version: str) -> None:
        super().__init__()
        self.service = _bounded_text(service)
        self.environment = _bounded_text(environment)
        self.app_version = _bounded_text(app_version)
        self._secrets = _known_secret_values()

    def _safe_event(self, record: logging.LogRecord) -> str:
        event = record.getMessage()
        for secret in self._secrets:
            event = event.replace(secret, "[REDACTED]")
        return _bounded_text(event, _MAX_EVENT_LENGTH)

    @staticmethod
    def _safe_exception(record: logging.LogRecord) -> dict[str, object] | None:
        if record.exc_info is None or record.exc_info[0] is None:
            return None

        exc_type, _, traceback = record.exc_info
        frames: list[dict[str, object]] = []
        while traceback is not None:
            frame = traceback.tb_frame
            frames.append(
                {
                    "file": Path(frame.f_code.co_filename).name,
                    "line": traceback.tb_lineno,
                    "function": frame.f_code.co_name,
                }
            )
            traceback = traceback.tb_next

        return {
            "type": exc_type.__name__,
            "traceback": frames[-_MAX_TRACEBACK_FRAMES:],
        }


class JsonLogFormatter(_SafeFormatter):
    """Serialize one secret-safe structured JSON record per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "event": self._safe_event(record),
            "service": self.service,
            "environment": self.environment,
            "version": self.app_version,
        }

        context = _LOG_CONTEXT.get() or {}
        for field in STRUCTURED_LOG_FIELDS:
            value = getattr(record, field, context.get(field))
            if value is not None:
                payload[field] = _safe_value(value)

        exception = self._safe_exception(record)
        if exception is not None:
            payload["exception"] = exception

        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class HumanLogFormatter(_SafeFormatter):
    """Render compact development logs while retaining safe lifecycle context."""

    def format(self, record: logging.LogRecord) -> str:
        context = _LOG_CONTEXT.get() or {}
        fields: list[str] = []
        for field in STRUCTURED_LOG_FIELDS:
            value = getattr(record, field, context.get(field))
            if value is not None:
                fields.append(f"{field}={_safe_value(value)}")

        suffix = f" [{' '.join(fields)}]" if fields else ""
        output = f"{record.levelname:<8} {record.name}: {self._safe_event(record)}{suffix}"
        exception = self._safe_exception(record)
        if exception is not None:
            frames = " -> ".join(
                f"{frame['file']}:{frame['line']}:{frame['function']}"
                for frame in exception["traceback"]
            )
            output = f"{output}\n{exception['type']}: {frames}"
        return output


def bind_log_context(**fields: object) -> Token[dict[str, object] | None]:
    """Bind approved lifecycle fields to logs emitted in the current context."""
    approved = {
        key: value
        for key, value in fields.items()
        if key in STRUCTURED_LOG_FIELDS and value is not None
    }
    return _LOG_CONTEXT.set({**(_LOG_CONTEXT.get() or {}), **approved})


def reset_log_context(token: Token[dict[str, object] | None]) -> None:
    """Restore the logging context that preceded a bind operation."""
    _LOG_CONTEXT.reset(token)


@contextmanager
def log_context(**fields: object) -> Iterator[None]:
    """Temporarily add approved lifecycle fields to every emitted record."""
    token = bind_log_context(**fields)
    try:
        yield
    finally:
        reset_log_context(token)


def configure_logging(*, force: bool = False) -> None:
    """Configure application and third-party logs for the current environment."""
    global _configured
    if _configured and not force:
        return

    environment = os.getenv("APP_ENVIRONMENT", "development").strip().casefold()
    service = os.getenv("LOG_SERVICE", "chess-lab").strip() or "chess-lab"
    level_name = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    formatter: logging.Formatter
    if environment == "production":
        formatter = JsonLogFormatter(
            service=service,
            environment=environment,
            app_version=_package_version(),
        )
    else:
        formatter = HumanLogFormatter(
            service=service,
            environment=environment,
            app_version=_package_version(),
        )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "celery"):
        third_party_logger = logging.getLogger(logger_name)
        third_party_logger.handlers.clear()
        third_party_logger.propagate = True

    # Request lifecycle logging is owned by the application middleware. HTTP
    # client internals and SQL statements are useful only when explicitly
    # enabled during diagnosis, never as production INFO noise.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    _configured = True
