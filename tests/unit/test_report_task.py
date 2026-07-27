from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import DEFAULT, MagicMock

import pytest

from app.services.llm.base import LLMError

PLAYER = "hero"
LANGUAGE = "en"


@pytest.fixture
def patched_task(monkeypatch):
    """Patch the task's collaborators and expose the mocks for assertions.

    Session open/close and the LLM call are recorded in one ``events`` list, so
    "the LLM never runs inside a transaction" is assertable from the ordering.
    """
    import app.tasks.celery_app as celery_app

    session = MagicMock()
    events: list[str] = []

    @contextmanager
    def fake_sync_session():
        events.append("session_enter")
        try:
            yield session
        finally:
            events.append("session_exit")

    monkeypatch.setattr(celery_app, "get_sync_db_session", fake_sync_session)

    build_ctx = MagicMock()
    provider = MagicMock()

    def _generate(system, user):
        events.append("llm")
        return DEFAULT  # defer to whatever the test set as return_value

    provider.generate.side_effect = _generate
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
        events=events,
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


@pytest.mark.unit
@pytest.mark.parametrize("failing_phase", ["context", "llm", "save"])
def test_llm_task_marks_failed_on_any_exception(patched_task, failing_phase):
    """Any phase blowing up must land in `failed`, not leave the row generating."""
    patched_task.build_ctx.return_value = SimpleNamespace(
        analyzed_games_count=5,
        last_game_played_at=None,
    )
    patched_task.provider.generate.return_value = "text"
    boom = RuntimeError("boom")
    match failing_phase:
        case "context":
            patched_task.build_ctx.side_effect = boom
        case "llm":
            patched_task.provider.generate.side_effect = boom
        case "save":
            patched_task.save_result.side_effect = boom

    # Swallowed, like LLMError: a failed report must not kill the worker.
    assert patched_task.celery_app.generate_player_report(PLAYER, LANGUAGE) is None

    patched_task.mark_failed.assert_called_once_with(
        patched_task.session, PLAYER, LANGUAGE
    )


@pytest.mark.unit
def test_session_not_held_during_llm(patched_task):
    """The LLM call runs between two short transactions, never inside one."""
    patched_task.build_ctx.return_value = SimpleNamespace(
        analyzed_games_count=5,
        last_game_played_at=None,
    )
    patched_task.provider.generate.return_value = "text"

    patched_task.celery_app.generate_player_report(PLAYER, LANGUAGE)

    assert patched_task.events == [
        "session_enter",
        "session_exit",
        "llm",
        "session_enter",
        "session_exit",
    ]


@pytest.mark.unit
def test_failure_marked_in_a_fresh_session(patched_task):
    """The session that failed is gone — the `failed` write gets its own txn."""
    patched_task.build_ctx.return_value = SimpleNamespace(
        analyzed_games_count=5,
        last_game_played_at=None,
    )
    patched_task.provider.generate.side_effect = LLMError("boom")

    patched_task.celery_app.generate_player_report(PLAYER, LANGUAGE)

    assert patched_task.events == [
        "session_enter",  # phase A: context
        "session_exit",
        "session_enter",  # the failure mark, after the LLM call blew up
        "session_exit",
    ]
