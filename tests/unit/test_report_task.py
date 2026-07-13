from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.llm.base import LLMError

PLAYER = "hero"
LANGUAGE = "en"


@pytest.fixture
def patched_task(monkeypatch):
    """Patch the task's collaborators and expose the mocks for assertions."""
    import app.tasks.celery_app as celery_app

    session = MagicMock()

    @contextmanager
    def fake_sync_session():
        yield session

    monkeypatch.setattr(celery_app, "get_sync_db_session", fake_sync_session)

    build_ctx = MagicMock()
    provider = MagicMock()
    get_provider = MagicMock(return_value=provider)
    build_messages = MagicMock(return_value=("system", "user"))
    save_result = MagicMock()
    mark_failed = MagicMock()

    monkeypatch.setattr(celery_app, "build_report_context", build_ctx)
    monkeypatch.setattr(celery_app, "get_llm_provider", get_provider)
    monkeypatch.setattr(celery_app, "build_messages", build_messages)
    monkeypatch.setattr(celery_app, "save_report_result_sync", save_result)
    monkeypatch.setattr(celery_app, "mark_failed_sync", mark_failed)

    return SimpleNamespace(
        celery_app=celery_app,
        session=session,
        build_ctx=build_ctx,
        provider=provider,
        get_provider=get_provider,
        build_messages=build_messages,
        save_result=save_result,
        mark_failed=mark_failed,
    )


@pytest.mark.unit
def test_generate_report_calls_provider_and_saves(patched_task):
    ctx = SimpleNamespace(
        analyzed_games_count=12,
        last_game_played_at=date(2026, 1, 1),
    )
    patched_task.build_ctx.return_value = ctx
    patched_task.provider.generate.return_value = "text"

    patched_task.celery_app.generate_player_report(PLAYER, LANGUAGE)

    patched_task.provider.generate.assert_called_once_with("system", "user")
    patched_task.save_result.assert_called_once()
    _, kwargs = patched_task.save_result.call_args
    assert kwargs["report_text"] == "text"
    assert kwargs["analyzed_games_count"] == 12
    assert kwargs["last_game_played_at"] == date(2026, 1, 1)
    patched_task.mark_failed.assert_not_called()


@pytest.mark.unit
def test_generate_report_zero_games_marks_failed(patched_task):
    patched_task.build_ctx.return_value = SimpleNamespace(
        analyzed_games_count=0,
        last_game_played_at=None,
    )

    patched_task.celery_app.generate_player_report(PLAYER, LANGUAGE)

    patched_task.get_provider.assert_not_called()
    patched_task.provider.generate.assert_not_called()
    patched_task.save_result.assert_not_called()
    patched_task.mark_failed.assert_called_once_with(
        patched_task.session, PLAYER, LANGUAGE
    )


@pytest.mark.unit
def test_generate_report_llmerror_marks_failed(patched_task):
    patched_task.build_ctx.return_value = SimpleNamespace(
        analyzed_games_count=5,
        last_game_played_at=None,
    )
    patched_task.provider.generate.side_effect = LLMError("boom")

    # Must not propagate — the worker stays alive.
    result = patched_task.celery_app.generate_player_report(PLAYER, LANGUAGE)

    assert result is None
    patched_task.save_result.assert_not_called()
    patched_task.mark_failed.assert_called_once_with(
        patched_task.session, PLAYER, LANGUAGE
    )
