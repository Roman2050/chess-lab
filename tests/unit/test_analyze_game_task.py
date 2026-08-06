"""Unit tests for the three-phase `analyze_game` task.

No DB and no engine: the sync session factory is replaced by a fake that records
when sessions open and close, so both the atomic claim and the "Stockfish never
runs inside a transaction" rule are assertable from the call ordering.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.models.db import ANALYSIS_STATUS_CLAIMABLE

GAME_ID = 42
PGN = "1. e4 e5 2. Nf3 Nc6"
RAW_MOVES = [{"ply": 1, "san": "e4"}]
ANALYSIS_DATA = {"summary": {"white_acpl": 12.0}, "moves": RAW_MOVES}


def _params(stmt) -> dict:
    """Bound parameters of a statement, keyed by column name."""
    return stmt.compile(dialect=postgresql.dialect()).params


@pytest.fixture
def task_env(monkeypatch):
    """Patch the task's session factory and collaborators; record the ordering."""
    import app.tasks.celery_app as celery_app

    monkeypatch.setattr(celery_app.settings, "STOCKFISH_PATH", "/usr/bin/stockfish")

    events: list[str] = []
    statements: list = []
    analysed_pgns: list[str] = []
    state = {"claim_result": PGN, "engine_error": None}

    class _FakeSession:
        def execute(self, stmt):
            statements.append(stmt)
            result = MagicMock()
            # Only the claim reads a value back; the writes ignore the result.
            result.scalar_one_or_none.return_value = state["claim_result"]
            return result

    @contextmanager
    def fake_sync_session():
        events.append("session_enter")
        try:
            yield _FakeSession()
        finally:
            events.append("session_exit")

    monkeypatch.setattr(celery_app, "get_sync_db_session", fake_sync_session)

    worker_engine = MagicMock()

    def _analyse_game(pgn_content: str, *, engine) -> list[dict]:
        events.append("engine")
        analysed_pgns.append(pgn_content)
        if state["engine_error"] is not None:
            raise state["engine_error"]
        return RAW_MOVES

    engine = MagicMock()
    engine.open_engine.return_value = worker_engine
    engine.analyse_game.side_effect = _analyse_game
    engine_cls = MagicMock(return_value=engine)
    monkeypatch.setattr(celery_app, "StockfishEngine", engine_cls)
    monkeypatch.setattr(celery_app, "_worker_engine", None)

    build_data = MagicMock(return_value=ANALYSIS_DATA)
    monkeypatch.setattr(celery_app, "build_analysis_data", build_data)

    return SimpleNamespace(
        module=celery_app,
        events=events,
        statements=statements,
        analysed_pgns=analysed_pgns,
        state=state,
        engine=engine,
        engine_cls=engine_cls,
        worker_engine=worker_engine,
        build_data=build_data,
    )


@pytest.mark.unit
def test_success_sets_completed_and_is_analyzed(task_env):
    """Phase C writes analysis_data, is_analyzed and `completed` together."""
    task_env.module.analyze_game(GAME_ID)

    assert task_env.analysed_pgns == [PGN]
    assert len(task_env.statements) == 2  # claim + save

    save = _params(task_env.statements[1])
    assert save["analysis_data"] == ANALYSIS_DATA
    assert save["is_analyzed"] is True
    assert save["analysis_status"] == "completed"
    assert save["analysis_error"] is None
    assert save["id_1"] == GAME_ID


@pytest.mark.unit
def test_engine_created_only_after_successful_claim(task_env):
    """No PGN back from the claim → another worker owns the game; do nothing."""
    task_env.state["claim_result"] = None

    assert task_env.module.analyze_game(GAME_ID) is None

    task_env.engine_cls.assert_not_called()
    task_env.engine.open_engine.assert_not_called()
    assert "engine" not in task_env.events
    assert len(task_env.statements) == 1  # the claim attempt only


@pytest.mark.unit
def test_completed_game_not_reclaimed(task_env):
    """The claim predicate accepts pending/failed only — never completed/running."""
    sql = str(
        task_env.module._claim_stmt(GAME_ID).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    assert "games.analysis_status IN ('pending', 'failed')" in sql
    assert "completed" not in sql
    assert ANALYSIS_STATUS_CLAIMABLE == ("pending", "failed")
    assert "analysis_attempts + 1" in sql


@pytest.mark.unit
def test_failure_marks_failed_and_reraises(task_env):
    """A crashed analysis lands in `failed` and still propagates to Celery."""
    task_env.state["engine_error"] = RuntimeError("engine died")

    with pytest.raises(RuntimeError, match="engine died"):
        task_env.module.analyze_game(GAME_ID)

    assert len(task_env.statements) == 2  # claim + failure mark
    failure = _params(task_env.statements[1])
    assert failure["analysis_status"] == "failed"
    assert failure["analysis_error"] == "engine died"
    assert "is_analyzed" not in failure


@pytest.mark.unit
def test_failure_message_is_truncated(task_env):
    """A giant traceback must not be copied wholesale into the status column."""
    task_env.state["engine_error"] = RuntimeError("x" * 5000)

    with pytest.raises(RuntimeError):
        task_env.module.analyze_game(GAME_ID)

    failure = _params(task_env.statements[1])
    assert len(failure["analysis_error"]) == task_env.module._ANALYSIS_ERROR_MAX_LEN


@pytest.mark.unit
def test_no_session_open_during_engine(task_env):
    """Stockfish runs between two transactions, never inside one."""
    task_env.module.analyze_game(GAME_ID)

    assert task_env.events == [
        "session_enter",
        "session_exit",
        "engine",
        "session_enter",
        "session_exit",
    ]


@pytest.mark.unit
def test_missing_stockfish_path_does_not_claim(task_env, monkeypatch):
    """Without an engine binary the game must stay claimable, not go `running`."""
    monkeypatch.setattr(task_env.module.settings, "STOCKFISH_PATH", "")

    assert task_env.module.analyze_game(GAME_ID) is None

    assert task_env.events == []
    assert task_env.statements == []
    task_env.engine_cls.assert_not_called()


@pytest.mark.unit
def test_task_routes_configured(task_env):
    assert task_env.module.analyze_game.name == (
        "app.tasks.celery_app.analyze_game"
    )
    assert task_env.module.generate_player_report.name == (
        "app.tasks.celery_app.generate_player_report"
    )
    assert task_env.module.celery_app.conf.task_routes == {
        "app.tasks.celery_app.analyze_game": {"queue": "analysis"},
        "app.tasks.celery_app.generate_player_report": {"queue": "reports"},
    }


@pytest.mark.unit
def test_long_task_delivery_configured(task_env):
    conf = task_env.module.celery_app.conf

    assert conf.worker_prefetch_multiplier == 1
    assert conf.task_acks_late is False
    assert conf.task_reject_on_worker_lost is False
    assert conf.result_backend is None


@pytest.mark.unit
def test_engine_reused_between_tasks(task_env):
    task_env.module.analyze_game(GAME_ID)
    task_env.module.analyze_game(GAME_ID + 1)

    task_env.engine.open_engine.assert_called_once_with()
    task_env.worker_engine.ping.assert_called_once_with()
    assert task_env.engine.analyse_game.call_count == 2
    assert [
        call.kwargs["engine"] for call in task_env.engine.analyse_game.call_args_list
    ] == [task_env.worker_engine, task_env.worker_engine]


@pytest.mark.unit
def test_engine_recreated_after_failed_ping(task_env):
    replacement = MagicMock()
    task_env.engine.open_engine.side_effect = [
        task_env.worker_engine,
        replacement,
    ]

    task_env.module.analyze_game(GAME_ID)
    task_env.worker_engine.ping.side_effect = RuntimeError("engine died")
    task_env.module.analyze_game(GAME_ID + 1)

    task_env.worker_engine.quit.assert_called_once_with()
    assert task_env.engine.open_engine.call_count == 2
    second_engine = task_env.engine.analyse_game.call_args_list[1].kwargs["engine"]
    assert second_engine is replacement


@pytest.mark.unit
@pytest.mark.parametrize("quit_error", [None, RuntimeError("quit failed")])
def test_worker_shutdown_closes_owned_engine(task_env, quit_error):
    task_env.module._worker_engine = task_env.worker_engine
    task_env.worker_engine.quit.side_effect = quit_error

    task_env.module._shutdown_worker_engine()
    task_env.module._shutdown_worker_engine()

    task_env.worker_engine.quit.assert_called_once_with()
    assert task_env.module._worker_engine is None
